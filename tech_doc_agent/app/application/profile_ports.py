from collections.abc import Sequence
from typing import Protocol

from tech_doc_agent.app.application.learning_ports import MemoryReaderPort
from tech_doc_agent.app.application.profile_models import (
    UserProfile,
    UserProfileUpdateResult,
)
from tech_doc_agent.app.core.tenant import TenantContext


class UserProfileRepositoryPort(Protocol):
    def get(self, tenant: TenantContext) -> UserProfile: ...

    def save(self, profile: UserProfile) -> None: ...


ProfileMemoryReaderPort = MemoryReaderPort


class UserProfileServicePort(Protocol):
    def get_profile(
        self,
        *,
        user_id: str,
        namespace: str,
    ) -> UserProfile: ...

    def update_profile(
        self,
        *,
        user_id: str,
        namespace: str,
        experience_level: str | None = None,
        explanation_style: str | None = None,
        depth: str | None = None,
        language: str | None = None,
        known_topics: Sequence[str] | None = None,
        weak_topics: Sequence[str] | None = None,
        resolved_weak_topics: Sequence[str] | None = None,
        notes: str | None = None,
        evidence: str | None = None,
    ) -> UserProfileUpdateResult: ...

    def context_summary(
        self,
        *,
        user_id: str,
        namespace: str,
        memory_query: str = "",
        memory_limit: int = 5,
    ) -> str: ...


__all__ = [
    "ProfileMemoryReaderPort",
    "UserProfileRepositoryPort",
    "UserProfileServicePort",
]
