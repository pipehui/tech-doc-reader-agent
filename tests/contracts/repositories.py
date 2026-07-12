from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeAlias

import pytest

from tech_doc_agent.app.application.approval_models import (
    ApprovalRepository,
    GuardrailApprovalRequest,
)
from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment
from tech_doc_agent.app.application.learning_state import (
    LearningStateRepositoryPort,
    LearningStateSnapshot,
)
from tech_doc_agent.app.application.profile_models import (
    UserProfile,
    UserProfileUpdate,
)
from tech_doc_agent.app.application.profile_service import UserProfileRepositoryPort
from tech_doc_agent.app.core.tenant import TenantContext


ApprovalRepositoryFactory: TypeAlias = Callable[[], ApprovalRepository]
LearningRepositoryFactory: TypeAlias = Callable[[], LearningStateRepositoryPort]
ProfileRepositoryFactory: TypeAlias = Callable[[], UserProfileRepositoryPort]


class ApprovalRepositoryContract:
    @pytest.fixture
    def approval_repository_factory(self) -> ApprovalRepositoryFactory:
        raise NotImplementedError

    def test_missing_roundtrip_overwrite_and_key_isolation(
        self,
        approval_repository_factory: ApprovalRepositoryFactory,
    ) -> None:
        repository = approval_repository_factory()
        first = _approval_request("session-1", "message-1")
        replacement = _approval_request("session-1", "message-2")
        isolated = _approval_request("session-2", "message-3")
        try:
            assert repository.get("missing") is None

            repository.put("key-1", first)
            repository.put("key-2", isolated)
            assert repository.get("key-1") == first
            assert repository.get("key-2") == isolated

            repository.put("key-1", replacement)
            assert repository.get("key-1") == replacement
            assert repository.get("key-2") == isolated
        finally:
            repository.close()

    def test_pop_is_atomic_and_one_shot(
        self,
        approval_repository_factory: ApprovalRepositoryFactory,
    ) -> None:
        repository = approval_repository_factory()
        request = _approval_request("session-pop", "message")
        try:
            repository.put("key", request)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: repository.pop("key"), range(2)))

            assert results.count(request) == 1
            assert results.count(None) == 1
            assert repository.get("key") is None
            assert repository.pop("key") is None
        finally:
            repository.close()


class LearningStateRepositoryContract:
    @pytest.fixture
    def learning_repository_factory(self) -> LearningRepositoryFactory:
        raise NotImplementedError

    def test_empty_roundtrip_latest_generation_and_detached_results(
        self,
        learning_repository_factory: LearningRepositoryFactory,
    ) -> None:
        repository = learning_repository_factory()
        assert repository.load() is None
        candidate = _learning_snapshot()

        first = repository.save(candidate)

        assert candidate.generation is None
        assert first.generation is not None
        assert first.records == candidate.records
        assert first.memories == candidate.memories
        assert first.processed_commands == candidate.processed_commands

        first.records.clear()
        first.memories.clear()
        first.processed_commands["a" * 64]["result"]["learning_message"] = "mutated"
        reloaded = learning_repository_factory().load()

        assert reloaded is not None
        assert len(reloaded.records) == 1
        assert len(reloaded.memories) == 1
        assert (
            reloaded.processed_commands["a" * 64]["result"]["learning_message"]
            == "saved"
        )

        updated = LearningStateSnapshot(
            records=[
                reloaded.records[0].reviewed(
                    timestamp="2026-07-13T00:00:00Z",
                    score=0.9,
                )
            ],
            memories=list(reloaded.memories),
            processed_commands=reloaded.processed_commands,
            generation=reloaded.generation,
        )
        second = learning_repository_factory().save(updated)
        latest = learning_repository_factory().load()

        assert second.generation is not None
        assert second.generation != reloaded.generation
        assert latest is not None
        assert latest.generation == second.generation
        assert latest.records[0].reviewtimes == 2
        assert latest.records[0].score == 0.9


class UserProfileRepositoryContract:
    @pytest.fixture
    def profile_repository_factory(self) -> ProfileRepositoryFactory:
        raise NotImplementedError

    def test_defaults_roundtrip_overwrite_tenant_isolation_and_detached_payload(
        self,
        profile_repository_factory: ProfileRepositoryFactory,
    ) -> None:
        tenant_a = TenantContext("user-a", "docs-a")
        tenant_b = TenantContext("user-a", "docs-b")
        repository = profile_repository_factory()

        assert repository.get(tenant_a) == UserProfile.default(tenant_a)
        assert repository.get(tenant_b) == UserProfile.default(tenant_b)

        profile = UserProfile.from_payload(
            {
                "experience_level": "进阶",
                "known_topics": ["StateGraph"],
            },
            tenant=tenant_a,
        )
        repository.save(profile)

        reloaded = profile_repository_factory().get(tenant_a)
        assert reloaded == profile
        assert profile_repository_factory().get(tenant_b) == UserProfile.default(tenant_b)

        payload = reloaded.to_payload()
        payload["known_topics"].append("mutated")
        assert profile_repository_factory().get(tenant_a).known_topics == ("StateGraph",)

        result = reloaded.apply(
            UserProfileUpdate.create(
                experience_level="专家",
                weak_topics=["Checkpoint"],
                evidence="contract update",
            ),
            timestamp="2026-07-13T00:00:00+00:00",
        )
        profile_repository_factory().save(result.profile)
        latest = profile_repository_factory().get(tenant_a)

        assert latest.experience_level == "专家"
        assert latest.known_topics == ("StateGraph",)
        assert latest.weak_topics == ("Checkpoint",)
        assert latest.last_update_reason == "contract update"
        assert latest.updated_at == "2026-07-13T00:00:00+00:00"


def _approval_request(session_id: str, user_input: str) -> GuardrailApprovalRequest:
    return GuardrailApprovalRequest.create(
        session_id=session_id,
        user_input=user_input,
        tenant=TenantContext("user-a", "docs-a"),
        source="chat.message",
        risk_level="medium",
        findings=["rule-a"],
    )


def _learning_snapshot() -> LearningStateSnapshot:
    tenant = TenantContext("user-a", "docs-a")
    return LearningStateSnapshot(
        records=[
            LearningRecord.create(
                knowledge="StateGraph",
                timestamp="2026-07-12T00:00:00Z",
                score=0.8,
                tenant=tenant,
            )
        ],
        memories=[
            MemoryFragment.create(
                kind="learned",
                topic="StateGraph",
                content="understood reducers",
                confidence=0.9,
                source_session_id="session-1",
                tenant=tenant,
                timestamp="2026-07-12T00:00:00Z",
                memory_id="memory-1",
            )
        ],
        processed_commands={
            "a" * 64: {
                "fingerprint": "b" * 64,
                "completed_at": "2026-07-12T00:00:00Z",
                "result": {
                    "learning_message": "saved",
                    "memory_message": "saved",
                    "memory_id": "memory-1",
                },
            }
        },
    )


__all__ = [
    "ApprovalRepositoryContract",
    "ApprovalRepositoryFactory",
    "LearningRepositoryFactory",
    "LearningStateRepositoryContract",
    "ProfileRepositoryFactory",
    "UserProfileRepositoryContract",
]
