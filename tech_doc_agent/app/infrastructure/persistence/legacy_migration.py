from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import shutil
from typing import Any, Literal
from urllib.parse import unquote

from tech_doc_agent.app.application.learning_state import LearningStateSnapshot
from tech_doc_agent.app.application.profile_models import UserProfile
from tech_doc_agent.app.core.errors import (
    ApplicationError,
    Conflict,
    ValidationError,
    classify_error,
)
from tech_doc_agent.app.core.tenant import DEFAULT_NAMESPACE, TenantContext
from tech_doc_agent.app.infrastructure.persistence.atomic_json import read_json
from tech_doc_agent.app.infrastructure.persistence.learning_state_repository import (
    LearningStateSnapshotRepository,
)
from tech_doc_agent.app.infrastructure.persistence.user_profile_repository import (
    USER_PROFILE_SCHEMA_VERSION,
    JsonUserProfileRepository,
)


MigrationStatus = Literal["planned", "migrated", "current", "shadowed"]
MigrationDomain = Literal["learning_state", "user_profile"]
MIGRATION_REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    domain: MigrationDomain
    sources: tuple[str, ...]
    target: str
    status: MigrationStatus
    detail: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "sources": list(self.sources),
            "target": self.target,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class MigrationReport:
    dry_run: bool
    backup_dir: str | None
    entries: tuple[MigrationEntry, ...]

    @property
    def summary(self) -> dict[str, int]:
        counts = Counter(entry.status for entry in self.entries)
        return {
            "planned": counts["planned"],
            "migrated": counts["migrated"],
            "current": counts["current"],
            "shadowed": counts["shadowed"],
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": MIGRATION_REPORT_SCHEMA_VERSION,
            "dry_run": self.dry_run,
            "backup_dir": self.backup_dir,
            "summary": self.summary,
            "entries": [entry.to_payload() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class _FileVersion:
    path: Path
    digest: str

    @classmethod
    def capture(cls, path: Path) -> _FileVersion:
        return cls(path=path, digest=_file_digest(path))

    def verify(self) -> None:
        if not self.path.is_file() or _file_digest(self.path) != self.digest:
            raise Conflict(
                "A migration source changed after it was planned.",
                code="migration_source_changed",
                dependency="file_repository",
                cause_type="MigrationSourceChanged",
            )


@dataclass(frozen=True, slots=True)
class _MigrationAction:
    entry: MigrationEntry
    sources: tuple[_FileVersion, ...]
    target: Path
    value: LearningStateSnapshot | UserProfile
    target_must_be_absent: bool

    def verify(self) -> None:
        for source in self.sources:
            source.verify()
        source_paths = {source.path for source in self.sources}
        if (
            self.target_must_be_absent
            and self.target not in source_paths
            and self.target.exists()
        ):
            raise Conflict(
                "A migration target appeared after it was planned.",
                code="migration_target_changed",
                dependency="file_repository",
                cause_type="MigrationTargetAppeared",
            )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LegacyPersistenceMigrator:
    data_path: Path
    clock: Callable[[], datetime] = _utc_now

    def run(
        self,
        *,
        apply: bool = False,
        backup_dir: Path | None = None,
    ) -> MigrationReport:
        try:
            return self._run(apply=apply, backup_dir=backup_dir)
        except ApplicationError:
            raise
        except Exception as exc:
            raise classify_error(exc, dependency="file_repository") from exc

    def _run(
        self,
        *,
        apply: bool,
        backup_dir: Path | None,
    ) -> MigrationReport:
        actions, static_entries = self._plan()
        entries = tuple(
            sorted(
                (*static_entries, *(action.entry for action in actions)),
                key=lambda entry: (entry.domain, entry.target, entry.sources),
            )
        )
        if not apply:
            return MigrationReport(
                dry_run=True,
                backup_dir=None,
                entries=entries,
            )

        if not actions:
            return MigrationReport(
                dry_run=False,
                backup_dir=None,
                entries=entries,
            )

        resolved_backup_dir = backup_dir or self._default_backup_dir()
        self._validate_backup_dir(resolved_backup_dir)
        for action in actions:
            action.verify()
        self._backup_sources(actions, resolved_backup_dir)

        migrated_entries: list[MigrationEntry] = []
        learning_repository = LearningStateSnapshotRepository(self.data_path)
        profile_repository = JsonUserProfileRepository(self.data_path)
        for action in actions:
            action.verify()
            if isinstance(action.value, LearningStateSnapshot):
                learning_repository.save(action.value)
            else:
                profile_repository.save(action.value)
            migrated_entries.append(replace(action.entry, status="migrated"))

        final_entries = tuple(
            sorted(
                (*static_entries, *migrated_entries),
                key=lambda entry: (entry.domain, entry.target, entry.sources),
            )
        )
        return MigrationReport(
            dry_run=False,
            backup_dir=str(resolved_backup_dir),
            entries=final_entries,
        )

    def _plan(self) -> tuple[list[_MigrationAction], list[MigrationEntry]]:
        actions: list[_MigrationAction] = []
        static_entries: list[MigrationEntry] = []
        self._plan_learning(actions, static_entries)
        self._plan_profiles(actions, static_entries)
        return actions, static_entries

    def _plan_learning(
        self,
        actions: list[_MigrationAction],
        static_entries: list[MigrationEntry],
    ) -> None:
        repository = LearningStateSnapshotRepository(self.data_path)
        legacy_paths = tuple(
            path
            for path in (
                repository.legacy_records_path,
                repository.legacy_memories_path,
            )
            if path.is_file()
        )
        if repository.manifest_path.exists():
            repository.load()
            static_entries.append(
                MigrationEntry(
                    domain="learning_state",
                    sources=tuple(self._relative(path) for path in legacy_paths),
                    target=self._relative(repository.manifest_path),
                    status="current",
                    detail="A versioned learning-state generation is already current.",
                )
            )
            return
        if not legacy_paths:
            return

        snapshot = repository.load()
        if snapshot is None:
            raise _invalid_migration("MissingLegacyLearningState")
        entry = MigrationEntry(
            domain="learning_state",
            sources=tuple(self._relative(path) for path in legacy_paths),
            target=self._relative(repository.manifest_path),
            status="planned",
            detail="Publish legacy learning/memory JSON as one snapshot generation.",
        )
        actions.append(
            _MigrationAction(
                entry=entry,
                sources=tuple(_FileVersion.capture(path) for path in legacy_paths),
                target=repository.manifest_path,
                value=snapshot,
                target_must_be_absent=True,
            )
        )

    def _plan_profiles(
        self,
        actions: list[_MigrationAction],
        static_entries: list[MigrationEntry],
    ) -> None:
        repository = JsonUserProfileRepository(self.data_path)
        profiles_dir = self.data_path / "user_profiles"
        if not profiles_dir.is_dir():
            return

        tenant_paths = sorted(path for path in profiles_dir.glob("*/*.json") if path.is_file())
        for path in tenant_paths:
            tenant = TenantContext(
                unquote(path.parent.name),
                unquote(path.stem),
            )
            value = read_json(path)
            profile = repository.get(tenant)
            if _is_versioned_profile(value):
                static_entries.append(
                    MigrationEntry(
                        domain="user_profile",
                        sources=(self._relative(path),),
                        target=self._relative(path),
                        status="current",
                        detail="The tenant profile already uses the versioned envelope.",
                    )
                )
                continue
            entry = MigrationEntry(
                domain="user_profile",
                sources=(self._relative(path),),
                target=self._relative(path),
                status="planned",
                detail="Wrap the flat tenant profile in a versioned envelope.",
            )
            actions.append(
                _MigrationAction(
                    entry=entry,
                    sources=(_FileVersion.capture(path),),
                    target=path,
                    value=profile,
                    target_must_be_absent=False,
                )
            )

        root_paths = sorted(path for path in profiles_dir.glob("*.json") if path.is_file())
        for path in root_paths:
            tenant = TenantContext(unquote(path.stem), DEFAULT_NAMESPACE)
            target = repository.path_for(tenant)
            if target.exists():
                static_entries.append(
                    MigrationEntry(
                        domain="user_profile",
                        sources=(self._relative(path),),
                        target=self._relative(target),
                        status="shadowed",
                        detail=(
                            "A tenant-scoped default profile already exists; "
                            "the older root profile is left untouched."
                        ),
                    )
                )
                continue

            profile = repository.get(tenant)
            entry = MigrationEntry(
                domain="user_profile",
                sources=(self._relative(path),),
                target=self._relative(target),
                status="planned",
                detail="Copy the root legacy profile to the default tenant path.",
            )
            actions.append(
                _MigrationAction(
                    entry=entry,
                    sources=(_FileVersion.capture(path),),
                    target=target,
                    value=profile,
                    target_must_be_absent=True,
                )
            )

    def _backup_sources(
        self,
        actions: list[_MigrationAction],
        backup_dir: Path,
    ) -> None:
        versions = {
            version.path: version
            for action in actions
            for version in action.sources
        }
        for source, version in sorted(versions.items(), key=lambda item: str(item[0])):
            version.verify()
            destination = backup_dir / source.relative_to(self.data_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if not destination.is_file() or _file_digest(destination) != version.digest:
                    raise Conflict(
                        "A migration backup path contains different data.",
                        code="migration_backup_conflict",
                        dependency="file_repository",
                        cause_type="MigrationBackupConflict",
                    )
                continue
            shutil.copy2(source, destination)

    def _default_backup_dir(self) -> Path:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Migration clock must return a timezone-aware datetime.")
        stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return self.data_path / "migration_backups" / stamp

    def _validate_backup_dir(self, backup_dir: Path) -> None:
        if backup_dir.resolve() == self.data_path.resolve():
            raise ValidationError(
                "The migration backup directory cannot be the data directory itself.",
                code="migration_backup_invalid",
                dependency="file_repository",
                cause_type="MigrationBackupIsDataPath",
            )

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.data_path).as_posix()


def _is_versioned_profile(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema_version") == USER_PROFILE_SCHEMA_VERSION
        and isinstance(value.get("profile"), Mapping)
    )


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _invalid_migration(cause_type: str) -> ValidationError:
    return ValidationError(
        "The legacy persistence data cannot be migrated.",
        code="legacy_migration_invalid",
        retryable=False,
        dependency="file_repository",
        cause_type=cause_type,
    )


__all__ = [
    "LegacyPersistenceMigrator",
    "MIGRATION_REPORT_SCHEMA_VERSION",
    "MigrationEntry",
    "MigrationReport",
]
