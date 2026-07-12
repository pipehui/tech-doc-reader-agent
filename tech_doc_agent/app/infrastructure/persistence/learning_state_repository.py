from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

from tech_doc_agent.app.application.learning_commands import UpdateLearningStateResult
from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment
from tech_doc_agent.app.application.learning_unit_of_work import LearningStateSnapshot
from tech_doc_agent.app.core.errors import ApplicationError, ValidationError, classify_error
from tech_doc_agent.app.infrastructure.persistence.atomic_json import read_json, write_json_atomic
from tech_doc_agent.app.infrastructure.persistence.generations import (
    GenerationDraft,
    GenerationStore,
    is_generation_id,
)


LEARNING_STATE_SCHEMA_VERSION = 1
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


class LearningStateSnapshotRepository:
    """Persist learning records, memories and idempotency outcomes as one snapshot."""

    def __init__(self, data_path: Path) -> None:
        self.data_path = data_path
        self.store_dir = data_path / "learning_state"
        self._generations = GenerationStore(self.store_dir)
        self.generations_dir = self._generations.generations_dir
        self.manifest_path = self._generations.manifest_path
        self.legacy_records_path = data_path / "learning_store" / "records.json"
        self.legacy_memories_path = data_path / "memory_store" / "memories.json"

    def load(self) -> LearningStateSnapshot | None:
        try:
            if self._generations.has_current_manifest():
                manifest = self._read_manifest(self._generations.read_current_manifest())
                return self._load_generation(manifest)

            if not (self.legacy_records_path.exists() or self.legacy_memories_path.exists()):
                return None
            return self._load_legacy()
        except ApplicationError:
            raise
        except Exception as exc:
            raise classify_error(exc, dependency="file_repository") from exc

    def save(self, snapshot: LearningStateSnapshot) -> LearningStateSnapshot:
        try:
            with self._generations.draft() as draft:
                manifest = self._build_manifest(draft.generation, snapshot)
                self._validate_snapshot(snapshot, manifest)
                write_json_atomic(
                    draft.path / "state.json",
                    self._snapshot_payload(snapshot),
                )
                persisted = self._load_generation(manifest)
                self._publish_manifest(draft, manifest)
                return persisted
        except ApplicationError:
            raise
        except Exception as exc:
            raise classify_error(exc, dependency="file_repository") from exc

    def _load_generation(
        self,
        manifest: dict[str, Any],
    ) -> LearningStateSnapshot:
        generation = manifest["generation"]
        path = self._generations.generation_path(generation) / "state.json"
        if not path.is_file():
            raise _corrupt_learning_state("MissingSnapshotFile")
        snapshot = self._snapshot_from_value(
            read_json(path),
            generation=generation,
        )
        self._validate_snapshot(snapshot, manifest)
        return snapshot

    def _load_legacy(self) -> LearningStateSnapshot:
        records: Any = []
        memories: Any = []
        if self.legacy_records_path.exists():
            records = read_json(self.legacy_records_path)
        if self.legacy_memories_path.exists():
            memories = read_json(self.legacy_memories_path)

        snapshot = LearningStateSnapshot(
            records=_learning_rows(records, "InvalidLegacyRecords"),
            memories=_memory_rows(memories, "InvalidLegacyMemories"),
        )
        self._validate_snapshot(snapshot, expected_manifest=None)
        return snapshot

    def _snapshot_from_value(
        self,
        value: Any,
        *,
        generation: str,
    ) -> LearningStateSnapshot:
        if not isinstance(value, dict) or value.get("schema_version") != LEARNING_STATE_SCHEMA_VERSION:
            raise _corrupt_learning_state("InvalidSnapshotEnvelope")
        processed_commands = value.get("processed_commands")
        if not isinstance(processed_commands, dict) or any(
            not isinstance(key, str) or not isinstance(command, dict) for key, command in processed_commands.items()
        ):
            raise _corrupt_learning_state("InvalidProcessedCommands")
        return LearningStateSnapshot(
            records=_learning_rows(value.get("records"), "InvalidRecords"),
            memories=_memory_rows(value.get("memories"), "InvalidMemories"),
            processed_commands={key: dict(command) for key, command in processed_commands.items()},
            generation=generation,
        )

    def _snapshot_payload(
        self,
        snapshot: LearningStateSnapshot,
    ) -> dict[str, Any]:
        return {
            "schema_version": LEARNING_STATE_SCHEMA_VERSION,
            "records": [record.to_payload() for record in snapshot.records],
            "memories": [memory.to_payload() for memory in snapshot.memories],
            "processed_commands": snapshot.processed_commands,
        }

    def _read_manifest(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise _corrupt_learning_state("InvalidManifest")
        counts = value.get("counts")
        if (
            value.get("schema_version") != LEARNING_STATE_SCHEMA_VERSION
            or not is_generation_id(value.get("generation"))
            or not isinstance(value.get("created_at"), str)
            or not isinstance(counts, dict)
            or not _is_non_negative_int(counts.get("records"))
            or not _is_non_negative_int(counts.get("memories"))
            or not _is_non_negative_int(counts.get("processed_commands"))
        ):
            raise _corrupt_learning_state("InvalidManifest")
        return value

    def _build_manifest(
        self,
        generation: str,
        snapshot: LearningStateSnapshot,
    ) -> dict[str, Any]:
        return {
            "schema_version": LEARNING_STATE_SCHEMA_VERSION,
            "generation": generation,
            "created_at": datetime.now(UTC).isoformat(),
            "counts": {
                "records": len(snapshot.records),
                "memories": len(snapshot.memories),
                "processed_commands": len(snapshot.processed_commands),
            },
        }

    def _validate_snapshot(
        self,
        snapshot: LearningStateSnapshot,
        expected_manifest: dict[str, Any] | None,
    ) -> None:
        if any(not isinstance(record, LearningRecord) for record in snapshot.records):
            raise _corrupt_learning_state("InvalidRecords")
        if any(not isinstance(memory, MemoryFragment) for memory in snapshot.memories):
            raise _corrupt_learning_state("InvalidMemories")

        for key, command in snapshot.processed_commands.items():
            if not isinstance(command, dict):
                raise _corrupt_learning_state("InvalidProcessedCommand")
            fingerprint = command.get("fingerprint")
            owner_key = command.get("owner_key")
            if (
                not isinstance(key, str)
                or _DIGEST_PATTERN.fullmatch(key) is None
                or not isinstance(fingerprint, str)
                or _DIGEST_PATTERN.fullmatch(fingerprint) is None
                or (
                    owner_key is not None
                    and (
                        not isinstance(owner_key, str)
                        or _DIGEST_PATTERN.fullmatch(owner_key) is None
                    )
                )
                or not isinstance(command.get("completed_at"), str)
            ):
                raise _corrupt_learning_state("InvalidProcessedCommand")
            UpdateLearningStateResult.from_payload(
                command.get("result"),
                replayed=True,
            )

        if expected_manifest is None:
            return
        counts = expected_manifest["counts"]
        if (
            counts["records"] != len(snapshot.records)
            or counts["memories"] != len(snapshot.memories)
            or counts["processed_commands"] != len(snapshot.processed_commands)
        ):
            raise _corrupt_learning_state("ManifestSnapshotMismatch")

    def _publish_manifest(
        self,
        draft: GenerationDraft,
        manifest: dict[str, Any],
    ) -> None:
        draft.publish(manifest)


def _learning_rows(value: Any, cause_type: str) -> list[LearningRecord]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise _corrupt_learning_state(cause_type)
    try:
        return [LearningRecord.from_payload(row) for row in value]
    except (TypeError, ValueError) as exc:
        raise _corrupt_learning_state(cause_type) from exc


def _memory_rows(value: Any, cause_type: str) -> list[MemoryFragment]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise _corrupt_learning_state(cause_type)
    try:
        return [MemoryFragment.from_payload(row) for row in value]
    except (TypeError, ValueError) as exc:
        raise _corrupt_learning_state(cause_type) from exc


def _is_non_negative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _corrupt_learning_state(cause_type: str) -> ValidationError:
    return ValidationError(
        "The learning state snapshot is invalid.",
        code="learning_state_corrupt",
        retryable=False,
        dependency="file_repository",
        cause_type=cause_type,
    )


__all__ = [
    "LEARNING_STATE_SCHEMA_VERSION",
    "LearningStateSnapshotRepository",
]
