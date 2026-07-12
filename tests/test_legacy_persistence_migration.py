from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from scripts.migrate_legacy_persistence import main
from tech_doc_agent.app.core.errors import Conflict, ValidationError
from tech_doc_agent.app.core.tenant import TenantContext
from tech_doc_agent.app.infrastructure.persistence.learning_state_repository import (
    LearningStateSnapshotRepository,
)
from tech_doc_agent.app.infrastructure.persistence.legacy_migration import (
    LegacyPersistenceMigrator,
)
from tech_doc_agent.app.infrastructure.persistence.user_profile_repository import (
    JsonUserProfileRepository,
)


FIXED_NOW = datetime(2026, 7, 12, 8, 0, tzinfo=UTC)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _seed_legacy_data(data_path: Path) -> dict[str, Path]:
    paths = {
        "records": data_path / "learning_store" / "records.json",
        "memories": data_path / "memory_store" / "memories.json",
        "tenant_profile": (
            data_path
            / "user_profiles"
            / "user%3Aa"
            / "docs%3Aprivate.json"
        ),
        "root_profile": data_path / "user_profiles" / "user-root.json",
    }
    _write_json(
        paths["records"],
        [
            {
                "knowledge": "Legacy StateGraph",
                "timestamp": "2026-01-01T00:00:00Z",
                "score": 0.7,
                "reviewtimes": 1,
                "user_id": "user-a",
                "namespace": "docs-a",
            }
        ],
    )
    _write_json(
        paths["memories"],
        [
            {
                "id": "memory-legacy",
                "kind": "learned",
                "topic": "Legacy StateGraph",
                "content": "legacy memory",
                "confidence": 0.8,
                "user_id": "user-a",
                "namespace": "docs-a",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ],
    )
    _write_json(
        paths["tenant_profile"],
        {"experience_level": "进阶", "known_topics": ["StateGraph"]},
    )
    _write_json(
        paths["root_profile"],
        {"experience_level": "专家", "known_topics": ["FastAPI"]},
    )
    return paths


def _migrator(data_path: Path) -> LegacyPersistenceMigrator:
    return LegacyPersistenceMigrator(data_path, clock=lambda: FIXED_NOW)


def test_dry_run_reports_all_actions_without_writing(tmp_path):
    data_path = tmp_path / "data"
    paths = _seed_legacy_data(data_path)
    original = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}

    report = _migrator(data_path).run()

    assert report.dry_run is True
    assert report.backup_dir is None
    assert report.summary == {
        "planned": 3,
        "migrated": 0,
        "current": 0,
        "shadowed": 0,
    }
    assert not (data_path / "learning_state" / "current.json").exists()
    assert not (data_path / "user_profiles" / "user-root" / "tech_docs.json").exists()
    assert not (data_path / "migration_backups").exists()
    assert {
        name: path.read_text(encoding="utf-8") for name, path in paths.items()
    } == original


def test_apply_backs_up_sources_and_writes_versioned_targets(tmp_path):
    data_path = tmp_path / "data"
    backup_dir = tmp_path / "backup"
    paths = _seed_legacy_data(data_path)
    original = {name: path.read_bytes() for name, path in paths.items()}

    report = _migrator(data_path).run(apply=True, backup_dir=backup_dir)

    assert report.dry_run is False
    assert report.backup_dir == str(backup_dir)
    assert report.summary["migrated"] == 3
    for name, path in paths.items():
        relative = path.relative_to(data_path)
        assert (backup_dir / relative).read_bytes() == original[name]

    learning = LearningStateSnapshotRepository(data_path).load()
    assert learning is not None
    assert learning.generation is not None
    assert learning.records[0].knowledge == "Legacy StateGraph"
    assert learning.memories[0].id == "memory-legacy"

    tenant_profile_path = paths["tenant_profile"]
    tenant_envelope = json.loads(tenant_profile_path.read_text(encoding="utf-8"))
    assert tenant_envelope["schema_version"] == 1
    assert tenant_envelope["profile"]["experience_level"] == "进阶"

    root_target = data_path / "user_profiles" / "user-root" / "tech_docs.json"
    root_envelope = json.loads(root_target.read_text(encoding="utf-8"))
    assert root_envelope["schema_version"] == 1
    assert root_envelope["profile"]["experience_level"] == "专家"
    assert paths["root_profile"].read_bytes() == original["root_profile"]


def test_apply_is_idempotent_and_does_not_publish_another_generation(tmp_path):
    data_path = tmp_path / "data"
    backup_dir = tmp_path / "backup"
    paths = _seed_legacy_data(data_path)
    migrator = _migrator(data_path)
    first = migrator.run(apply=True, backup_dir=backup_dir)
    repository = LearningStateSnapshotRepository(data_path)
    first_manifest = repository.manifest_path.read_text(encoding="utf-8")
    tenant_profile = paths["tenant_profile"].read_text(encoding="utf-8")

    second = migrator.run(apply=True, backup_dir=backup_dir)

    assert first.summary["migrated"] == 3
    assert second.summary == {
        "planned": 0,
        "migrated": 0,
        "current": 3,
        "shadowed": 1,
    }
    assert second.backup_dir is None
    assert repository.manifest_path.read_text(encoding="utf-8") == first_manifest
    assert paths["tenant_profile"].read_text(encoding="utf-8") == tenant_profile
    assert len(list(repository.generations_dir.iterdir())) == 1


def test_planning_failure_does_not_backup_or_migrate_other_domains(tmp_path):
    data_path = tmp_path / "data"
    _seed_legacy_data(data_path)
    invalid_profile = data_path / "user_profiles" / "broken" / "docs.json"
    _write_json(
        invalid_profile,
        {"schema_version": 99, "profile": {}},
    )
    backup_dir = tmp_path / "backup"

    with pytest.raises(ValidationError) as raised:
        _migrator(data_path).run(apply=True, backup_dir=backup_dir)

    assert raised.value.code == "user_profile_corrupt"
    assert not backup_dir.exists()
    assert not (data_path / "learning_state" / "current.json").exists()


def test_backup_conflict_stops_before_any_target_is_written(tmp_path):
    data_path = tmp_path / "data"
    _seed_legacy_data(data_path)
    backup_dir = tmp_path / "backup"
    conflicting_backup = backup_dir / "learning_store" / "records.json"
    conflicting_backup.parent.mkdir(parents=True)
    conflicting_backup.write_text("different", encoding="utf-8")

    with pytest.raises(Conflict) as raised:
        _migrator(data_path).run(apply=True, backup_dir=backup_dir)

    assert raised.value.code == "migration_backup_conflict"
    assert not (data_path / "learning_state" / "current.json").exists()
    tenant_profile = JsonUserProfileRepository(data_path).get(
        TenantContext("user:a", "docs:private")
    )
    assert tenant_profile.experience_level == "进阶"


def test_source_fingerprint_change_stops_before_writes(tmp_path, monkeypatch):
    data_path = tmp_path / "data"
    paths = _seed_legacy_data(data_path)
    backup_dir = tmp_path / "backup"
    original_backup = LegacyPersistenceMigrator._backup_sources

    def mutate_before_backup(self, actions, resolved_backup_dir):
        paths["records"].write_text("[]", encoding="utf-8")
        return original_backup(self, actions, resolved_backup_dir)

    monkeypatch.setattr(
        LegacyPersistenceMigrator,
        "_backup_sources",
        mutate_before_backup,
    )

    with pytest.raises(Conflict) as raised:
        _migrator(data_path).run(apply=True, backup_dir=backup_dir)

    assert raised.value.code == "migration_source_changed"
    assert not (data_path / "learning_state" / "current.json").exists()
    assert not backup_dir.exists()


def test_cli_defaults_to_dry_run_and_can_write_summary(tmp_path, capsys):
    data_path = tmp_path / "data"
    _seed_legacy_data(data_path)
    summary_path = tmp_path / "migration-summary.json"

    exit_code = main(
        [
            "--data-path",
            str(data_path),
            "--summary-output",
            str(summary_path),
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    persisted = json.loads(summary_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert printed == persisted
    assert printed["schema_version"] == 1
    assert printed["dry_run"] is True
    assert printed["summary"]["planned"] == 3
    assert not (data_path / "learning_state" / "current.json").exists()
