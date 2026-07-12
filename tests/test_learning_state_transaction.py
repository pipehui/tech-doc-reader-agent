from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment
from tech_doc_agent.app.application.learning_state import (
    LearningStateService,
    LearningStateUnitOfWork,
    UpdateLearningStateCommand,
)
from tech_doc_agent.app.core.errors import ApplicationError, Conflict, ValidationError
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.tenant import TenantContext
from tech_doc_agent.app.infrastructure.persistence.learning_state_repository import (
    LearningStateSnapshotRepository,
)
from tech_doc_agent.app.services.resources import AppResources
from tech_doc_agent.app.services.vectordb.learning_store_backend import LearningStore
from tech_doc_agent.app.services.vectordb.memory_store_backend import MemoryStore
from tech_doc_agent.app.tools import ToolDependencies, build_tool_bundle


TENANT = TenantContext("user-a", "tenant-docs")


def _stack(tmp_path: Path):
    settings = Settings(DATA_PATH=str(tmp_path), SEED_DOC_STORE_ON_EMPTY=False)
    repository = LearningStateSnapshotRepository(tmp_path)
    unit_of_work = LearningStateUnitOfWork(repository)
    learning_store = LearningStore(settings, unit_of_work=unit_of_work)
    memory_store = MemoryStore(settings, unit_of_work=unit_of_work)
    service = LearningStateService(
        unit_of_work,
        learning_store,
        memory_store,
    )
    return repository, unit_of_work, learning_store, memory_store, service


def _command(
    *,
    tool_call_id: str = "call-1",
    session_id: str = "session-1",
    tenant: TenantContext = TENANT,
    score: float = 0.8,
    memory_content: str | None = "用户理解了 reducer 的累计语义。",
) -> UpdateLearningStateCommand:
    return UpdateLearningStateCommand(
        tenant=tenant,
        session_id=session_id,
        tool_call_id=tool_call_id,
        knowledge="LangGraph Reducer",
        timestamp="2026-07-12T10:00:00Z",
        score=score,
        memory_kind="learned",
        memory_topic="LangGraph Reducer",
        memory_content=memory_content,
        memory_confidence=0.9,
    )


def _manifest(repository: LearningStateSnapshotRepository) -> dict:
    return json.loads(repository.manifest_path.read_text(encoding="utf-8"))


def _state_payload(repository: LearningStateSnapshotRepository) -> dict:
    generation = _manifest(repository)["generation"]
    state_path = repository.generations_dir / generation / "state.json"
    return json.loads(state_path.read_text(encoding="utf-8"))


def _reload(tmp_path: Path):
    stack = _stack(tmp_path)
    _, unit_of_work, _, _, _ = stack
    assert unit_of_work.load() is True
    return stack


def test_learning_and_memory_commit_as_one_idempotent_generation(tmp_path):
    repository, unit_of_work, learning_store, memory_store, service = _stack(tmp_path)
    command = _command()

    first = service.update(command)
    first_generation = unit_of_work.generation

    assert first.replayed is False
    assert first.memory_id
    assert first_generation == _manifest(repository)["generation"]
    assert learning_store.records[0]["reviewtimes"] == 1
    assert memory_store.memories[0]["id"] == first.memory_id
    assert unit_of_work.processed_command_count == 1
    assert _manifest(repository)["counts"] == {
        "records": 1,
        "memories": 1,
        "processed_commands": 1,
    }

    replay = service.update(command)

    assert replay.replayed is True
    assert replay.message == first.message
    assert replay.memory_id == first.memory_id
    assert unit_of_work.generation == first_generation
    assert learning_store.records[0]["reviewtimes"] == 1
    assert len(memory_store.memories) == 1

    _, reloaded_uow, reloaded_learning, reloaded_memory, reloaded_service = _reload(tmp_path)
    restarted_replay = reloaded_service.update(command)

    assert restarted_replay.replayed is True
    assert restarted_replay.memory_id == first.memory_id
    assert reloaded_uow.generation == first_generation
    assert reloaded_learning.records[0]["reviewtimes"] == 1
    assert len(reloaded_memory.memories) == 1

    next_result = reloaded_service.update(_command(tool_call_id="call-2"))

    assert next_result.replayed is False
    assert reloaded_learning.records[0]["reviewtimes"] == 2
    assert len(reloaded_memory.memories) == 1
    assert reloaded_uow.processed_command_count == 2


def test_generation_keeps_json_schema_and_reloads_domain_models(tmp_path):
    repository, _, _, _, service = _stack(tmp_path)

    result = service.update(_command())
    payload = _state_payload(repository)

    assert payload["schema_version"] == 1
    assert payload["records"] == [
        {
            "knowledge": "LangGraph Reducer",
            "timestamp": "2026-07-12T10:00:00Z",
            "score": 0.8,
            "reviewtimes": 1,
            "user_id": "user-a",
            "namespace": "tenant-docs",
        }
    ]
    assert payload["memories"][0] == {
        "id": result.memory_id,
        "user_id": "user-a",
        "namespace": "tenant-docs",
        "kind": "learned",
        "topic": "LangGraph Reducer",
        "content": _command().memory_content,
        "confidence": 0.9,
        "source_session_id": "session-1",
        "created_at": payload["memories"][0]["created_at"],
        "updated_at": payload["memories"][0]["updated_at"],
    }

    snapshot = repository.load()

    assert snapshot is not None
    assert isinstance(snapshot.records[0], LearningRecord)
    assert isinstance(snapshot.memories[0], MemoryFragment)
    assert snapshot.records[0].to_payload() == payload["records"][0]
    assert snapshot.memories[0].to_payload() == payload["memories"][0]


def test_compatibility_views_cannot_mutate_active_domain_state(tmp_path):
    _, _, learning_store, memory_store, service = _stack(tmp_path)
    service.update(_command())

    record_rows = learning_store.records
    memory_rows = memory_store.memories
    record_rows[0]["score"] = 0.1
    record_rows.clear()
    memory_rows[0]["content"] = "mutated outside the unit of work"
    memory_rows.clear()

    assert len(learning_store.record_models) == 1
    assert learning_store.record_models[0].score == 0.8
    assert len(memory_store.memory_models) == 1
    assert memory_store.memory_models[0].content == _command().memory_content


def test_corrupt_reload_keeps_published_in_memory_snapshot(tmp_path):
    repository, unit_of_work, learning_store, _, service = _stack(tmp_path)
    service.update(_command(memory_content=None))
    generation = unit_of_work.generation
    records = unit_of_work.records
    state_path = repository.generations_dir / str(generation) / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["records"] = [42]
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError) as raised:
        unit_of_work.load()

    assert raised.value.code == "learning_state_corrupt"
    assert raised.value.cause_type == "InvalidRecords"
    assert unit_of_work.generation == generation
    assert unit_of_work.records == records
    assert learning_store.record_models == records


def test_same_idempotency_key_rejects_different_payload(tmp_path):
    repository, unit_of_work, learning_store, memory_store, service = _stack(tmp_path)
    service.update(_command())
    generation = unit_of_work.generation

    with pytest.raises(Conflict) as raised:
        service.update(_command(score=0.2))

    assert raised.value.code == "learning_idempotency_conflict"
    assert unit_of_work.generation == generation
    assert _manifest(repository)["generation"] == generation
    assert learning_store.records[0]["reviewtimes"] == 1
    assert len(memory_store.memories) == 1


def test_idempotency_identity_includes_tenant_session_and_tool_call():
    base = _command()

    assert base.idempotency_key() != _command(tenant=TenantContext("user-b", "tenant-docs")).idempotency_key()
    assert base.idempotency_key() != _command(session_id="session-2").idempotency_key()
    assert base.idempotency_key() != _command(tool_call_id="call-2").idempotency_key()
    assert base.owner_key() == _command(session_id="session-2", tool_call_id="call-2").owner_key()
    assert base.owner_key() != _command(tenant=TenantContext("user-b", "tenant-docs")).owner_key()


def test_processed_command_persists_deterministic_owner_key(tmp_path):
    repository, _, _, _, service = _stack(tmp_path)
    command = _command()

    service.update(command)
    commands = _state_payload(repository)["processed_commands"]
    persisted = next(iter(commands.values()))

    assert persisted["owner_key"] == command.owner_key()
    assert "user_id" not in persisted
    assert "namespace" not in persisted
    assert "session_id" not in persisted
    assert "tool_call_id" not in persisted


def test_repository_rejects_invalid_processed_command_owner_key(tmp_path):
    repository, _, _, _, service = _stack(tmp_path)
    service.update(_command())
    generation = _manifest(repository)["generation"]
    state_path = repository.generations_dir / generation / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    next(iter(payload["processed_commands"].values()))["owner_key"] = "invalid"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError) as raised:
        repository.load()

    assert raised.value.code == "learning_state_corrupt"
    assert raised.value.cause_type == "InvalidProcessedCommand"


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": ""},
        {"tool_call_id": ""},
        {"score": float("nan")},
        {"memory_confidence": float("inf")},
    ],
)
def test_command_rejects_missing_context_and_non_finite_values(changes):
    values = {
        "tenant": TENANT,
        "session_id": "session-1",
        "tool_call_id": "call-1",
        "knowledge": "LangGraph Reducer",
        "timestamp": "2026-07-12T10:00:00Z",
        **changes,
    }

    with pytest.raises(ValidationError) as raised:
        UpdateLearningStateCommand(**values)

    assert raised.value.code == "learning_command_invalid"
    assert raised.value.dependency == "learning_state_repository"


def test_memory_stage_failure_rolls_back_learning_candidate(
    tmp_path,
    monkeypatch,
):
    repository, unit_of_work, learning_store, memory_store, service = _stack(tmp_path)
    service.update(_command(tool_call_id="seed", memory_content=None))
    generation = unit_of_work.generation
    manifest_before = repository.manifest_path.read_text(encoding="utf-8")

    def fail_memory(*args, **kwargs):
        raise RuntimeError("injected memory mutation failure")

    monkeypatch.setattr(memory_store, "prepare_upsert_memory", fail_memory)

    with pytest.raises(RuntimeError):
        service.update(_command(tool_call_id="memory-fails"))

    assert unit_of_work.generation == generation
    assert unit_of_work.processed_command_count == 1
    assert learning_store.records[0]["reviewtimes"] == 1
    assert memory_store.memories == []
    assert repository.manifest_path.read_text(encoding="utf-8") == manifest_before


def test_manifest_publish_failure_keeps_disk_and_active_state_unchanged(
    tmp_path,
    monkeypatch,
):
    repository, unit_of_work, learning_store, memory_store, service = _stack(tmp_path)
    service.update(_command(tool_call_id="seed", memory_content=None))
    generation = unit_of_work.generation
    manifest_before = repository.manifest_path.read_text(encoding="utf-8")

    def fail_publish(*args, **kwargs):
        raise OSError("injected manifest failure")

    monkeypatch.setattr(repository, "_publish_manifest", fail_publish)

    with pytest.raises(ApplicationError):
        service.update(_command(tool_call_id="publish-fails"))

    assert unit_of_work.generation == generation
    assert unit_of_work.processed_command_count == 1
    assert learning_store.records[0]["reviewtimes"] == 1
    assert memory_store.memories == []
    assert repository.manifest_path.read_text(encoding="utf-8") == manifest_before
    assert [path.name for path in repository.generations_dir.iterdir()] == [generation]

    _, reloaded_uow, reloaded_learning, reloaded_memory, _ = _reload(tmp_path)
    assert reloaded_uow.generation == generation
    assert reloaded_learning.records[0]["reviewtimes"] == 1
    assert reloaded_memory.memories == []


def test_unreferenced_interrupted_generation_is_ignored(tmp_path):
    repository, unit_of_work, _, _, service = _stack(tmp_path)
    service.update(_command(memory_content=None))
    generation = unit_of_work.generation

    orphan = repository.generations_dir / uuid4().hex
    orphan.mkdir()
    (orphan / "state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [{"knowledge": "uncommitted"}],
                "memories": [],
                "processed_commands": {},
            }
        ),
        encoding="utf-8",
    )

    _, reloaded_uow, reloaded_learning, _, _ = _reload(tmp_path)
    assert reloaded_uow.generation == generation
    assert reloaded_learning.records[0]["knowledge"] == "LangGraph Reducer"


def test_legacy_json_pair_migrates_on_first_transaction(tmp_path):
    records_path = tmp_path / "learning_store" / "records.json"
    memories_path = tmp_path / "memory_store" / "memories.json"
    records_path.parent.mkdir(parents=True)
    memories_path.parent.mkdir(parents=True)
    records_path.write_text(
        json.dumps(
            [
                {
                    "knowledge": "Legacy Topic",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "score": 0.6,
                    "reviewtimes": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    memories_path.write_text("[]", encoding="utf-8")

    repository, unit_of_work, learning_store, memory_store, service = _stack(tmp_path)
    assert unit_of_work.load() is True
    assert unit_of_work.generation is None
    assert learning_store.records[0]["knowledge"] == "Legacy Topic"

    service.update(_command(memory_content=None))

    assert repository.manifest_path.is_file()
    assert records_path.is_file()
    assert memories_path.is_file()
    assert "LangGraph Reducer" not in records_path.read_text(encoding="utf-8")
    _, _, reloaded_learning, _, _ = _reload(tmp_path)
    assert {record["knowledge"] for record in reloaded_learning.records} == {
        "Legacy Topic",
        "LangGraph Reducer",
    }


def test_tool_call_id_is_injected_hidden_and_replayed_idempotently(tmp_path):
    resources = AppResources.create(Settings(DATA_PATH=str(tmp_path), SEED_DOC_STORE_ON_EMPTY=False))
    tools = build_tool_bundle(ToolDependencies.from_container(resources))
    tool = tools.upsert_learning_history
    config = {
        "metadata": {
            "user_id": "user-a",
            "namespace": "tenant-docs",
            "session_id": "session-tool",
        }
    }
    call = {
        "name": tool.name,
        "id": "call-tool",
        "type": "tool_call",
        "args": {
            "knowledge": "Idempotent Tool",
            "timestamp": "2026-07-12T10:00:00Z",
            "score": 0.9,
        },
    }

    model_schema = tool.tool_call_schema.model_json_schema()
    assert "tool_call_id" not in model_schema["properties"]
    first = tool.invoke(call, config=config)
    generation = resources.learning_store.unit_of_work.generation
    second = tool.invoke(call, config=config)

    assert second.content == first.content
    assert resources.learning_store.unit_of_work.generation == generation
    record = resources.learning_store.read_by_query(
        "Idempotent Tool",
        user_id="user-a",
        namespace="tenant-docs",
    )[0]
    assert record["reviewtimes"] == 1

    with pytest.raises(ValueError, match="InjectedToolCallId"):
        tool.invoke(call["args"], config=config)
