from tech_doc_agent.app.application.learning_models import (
    LearningRecord,
    MemoryFragment,
)
from tech_doc_agent.app.core.tenant import TenantContext


TENANT = TenantContext("user-a", "tenant-docs")


def test_learning_record_normalizes_legacy_payload_and_serializes_stable_schema():
    record = LearningRecord.from_payload(
        {
            "knowledge": " StateGraph ",
            "timestamp": "2026-07-12T10:00:00Z",
            "score": "not-a-number",
            "reviewtimes": -3,
            "user_id": "../invalid",
            "namespace": "docs/private",
            "ignored": "legacy extension",
        }
    )

    assert record.to_payload() == {
        "knowledge": "StateGraph",
        "timestamp": "2026-07-12T10:00:00Z",
        "score": 0.0,
        "reviewtimes": 0,
        "user_id": "default",
        "namespace": "tech_docs",
    }


def test_learning_record_review_returns_new_value_without_mutating_original():
    original = LearningRecord.create(
        knowledge="StateGraph",
        timestamp="2026-07-11T10:00:00Z",
        score=0.6,
        tenant=TENANT,
    )

    reviewed = original.reviewed(
        timestamp="2026-07-12T10:00:00Z",
        score=0.9,
    )

    assert original.reviewtimes == 1
    assert original.score == 0.6
    assert reviewed.reviewtimes == 2
    assert reviewed.score == 0.9
    assert reviewed.tenant is TENANT


def test_memory_fragment_normalizes_legacy_payload_and_clamps_confidence():
    memory = MemoryFragment.from_payload(
        {
            "id": "memory-1",
            "kind": "unknown-kind",
            "topic": " Reducer ",
            "content": " 累计更新 ",
            "confidence": 4,
            "updated_at": "2026-07-12T10:00:00Z",
            "user_id": "user-a",
            "namespace": "tenant-docs",
        }
    )

    assert memory.kind == "learned"
    assert memory.confidence == 1.0
    assert memory.created_at == memory.updated_at
    assert memory.to_payload()["user_id"] == "user-a"


def test_memory_update_preserves_identity_and_created_at():
    original = MemoryFragment.create(
        kind="stuck_point",
        topic="Reducer",
        content="旧观察",
        confidence=0.4,
        source_session_id="session-1",
        tenant=TENANT,
        timestamp="2026-07-11T10:00:00Z",
        memory_id="memory-1",
    )
    incoming = MemoryFragment.create(
        kind="stuck_point",
        topic="Reducer",
        content="新观察",
        confidence=0.8,
        source_session_id=None,
        tenant=TENANT,
        timestamp="2026-07-12T10:00:00Z",
        memory_id="ignored",
    )

    updated = original.updated_from(
        incoming,
        timestamp="2026-07-12T10:00:00Z",
    )

    assert updated.id == "memory-1"
    assert updated.created_at == "2026-07-11T10:00:00Z"
    assert updated.updated_at == "2026-07-12T10:00:00Z"
    assert updated.source_session_id == "session-1"
    assert updated.content == "新观察"
