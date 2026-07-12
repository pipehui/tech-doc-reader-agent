from __future__ import annotations

from collections.abc import Callable

import pytest

from tech_doc_agent.app.application.approval_models import ApprovalRepository
from tech_doc_agent.app.application.learning_state import LearningStateRepositoryPort
from tech_doc_agent.app.application.profile_service import UserProfileRepositoryPort
from tech_doc_agent.app.infrastructure.persistence.approval_repository import (
    RedisApprovalRepository,
)
from tech_doc_agent.app.infrastructure.persistence.in_memory_approval_repository import (
    InMemoryApprovalRepository,
)
from tech_doc_agent.app.infrastructure.persistence.learning_state_repository import (
    LearningStateSnapshotRepository,
)
from tech_doc_agent.app.infrastructure.persistence.user_profile_repository import (
    JsonUserProfileRepository,
)
from tests.contracts.repositories import (
    ApprovalRepositoryContract,
    LearningStateRepositoryContract,
    UserProfileRepositoryContract,
)
from tests.fakes.redis import FakeRedisBackend, FakeRedisClient


class TestInMemoryApprovalRepositoryContract(ApprovalRepositoryContract):
    @pytest.fixture
    def approval_repository_factory(self) -> Callable[[], ApprovalRepository]:
        return InMemoryApprovalRepository


class TestRedisApprovalRepositoryContract(ApprovalRepositoryContract):
    @pytest.fixture
    def approval_repository_factory(self) -> Callable[[], ApprovalRepository]:
        backend = FakeRedisBackend()
        return lambda: RedisApprovalRepository(
            client=FakeRedisClient(backend),
            ttl_seconds=60,
        )


class TestJsonLearningStateRepositoryContract(LearningStateRepositoryContract):
    @pytest.fixture
    def learning_repository_factory(
        self,
        tmp_path,
    ) -> Callable[[], LearningStateRepositoryPort]:
        return lambda: LearningStateSnapshotRepository(tmp_path)


class TestJsonUserProfileRepositoryContract(UserProfileRepositoryContract):
    @pytest.fixture
    def profile_repository_factory(
        self,
        tmp_path,
    ) -> Callable[[], UserProfileRepositoryPort]:
        return lambda: JsonUserProfileRepository(tmp_path)
