from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from tech_doc_agent.app.application.profile_models import (
    DEFAULT_DEPTH,
    DEFAULT_EXPERIENCE_LEVEL,
    DEFAULT_EXPLANATION_STYLE,
    DEFAULT_LANGUAGE,
    PROFILE_VERSION,
    UserProfile,
    UserProfileUpdateResult,
)
from tech_doc_agent.app.application.profile_service import (
    ProfileMemoryReaderPort,
    UserProfileService as ApplicationUserProfileService,
    format_user_profile_summary,
)
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.infrastructure.persistence.user_profile_repository import (
    JsonUserProfileRepository,
)


DEFAULT_PROFILE = {
    "experience_level": DEFAULT_EXPERIENCE_LEVEL,
    "explanation_style": DEFAULT_EXPLANATION_STYLE,
    "depth": DEFAULT_DEPTH,
    "language": DEFAULT_LANGUAGE,
    "known_topics": [],
    "weak_topics": [],
    "notes": "",
}


class UserProfileService:
    """Compatibility constructor around the injected application service."""

    def __init__(
        self,
        settings: Settings,
        memory_store: ProfileMemoryReaderPort | None = None,
    ) -> None:
        self.settings = settings
        self.memory_store = memory_store
        self._delegate = _build_service(settings, memory_store=memory_store)

    def get_profile(self, *, user_id: str, namespace: str) -> UserProfile:
        return self._delegate.get_profile(user_id=user_id, namespace=namespace)

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
    ) -> UserProfileUpdateResult:
        return self._delegate.update_profile(
            user_id=user_id,
            namespace=namespace,
            experience_level=experience_level,
            explanation_style=explanation_style,
            depth=depth,
            language=language,
            known_topics=known_topics,
            weak_topics=weak_topics,
            resolved_weak_topics=resolved_weak_topics,
            notes=notes,
            evidence=evidence,
        )

    def context_summary(
        self,
        *,
        user_id: str,
        namespace: str,
        memory_query: str = "",
        memory_limit: int = 5,
    ) -> str:
        return self._delegate.context_summary(
            user_id=user_id,
            namespace=namespace,
            memory_query=memory_query,
            memory_limit=memory_limit,
        )


def get_user_profile_summary(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    settings: Settings | None = None,
) -> str:
    service = _build_service(settings)
    profile = service.get_profile(
        user_id=user_id,
        namespace=namespace,
    )
    return format_user_profile_summary(profile)


def get_user_context_summary(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    memory_query: str = "",
    memory_limit: int = 5,
    settings: Settings | None = None,
    memory_store: ProfileMemoryReaderPort | None = None,
) -> str:
    service = _build_service(settings, memory_store=memory_store)
    return service.context_summary(
        user_id=user_id,
        namespace=namespace,
        memory_query=memory_query,
        memory_limit=memory_limit,
    )


def get_user_profile(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, object]:
    profile = _build_service(settings).get_profile(
        user_id=user_id,
        namespace=namespace,
    )
    return profile.to_payload()


def update_user_profile(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    experience_level: str | None = None,
    explanation_style: str | None = None,
    depth: str | None = None,
    language: str | None = None,
    known_topics: list[str] | None = None,
    weak_topics: list[str] | None = None,
    resolved_weak_topics: list[str] | None = None,
    notes: str | None = None,
    evidence: str | None = None,
    settings: Settings | None = None,
) -> dict[str, object]:
    result = _build_service(settings).update_profile(
        user_id=user_id,
        namespace=namespace,
        experience_level=experience_level,
        explanation_style=explanation_style,
        depth=depth,
        language=language,
        known_topics=known_topics,
        weak_topics=weak_topics,
        resolved_weak_topics=resolved_weak_topics,
        notes=notes,
        evidence=evidence,
    )
    return result.to_payload()


def _build_service(
    settings: Settings | None,
    *,
    memory_store: ProfileMemoryReaderPort | None = None,
) -> ApplicationUserProfileService:
    resolved = settings or get_settings()
    return ApplicationUserProfileService(
        repository=JsonUserProfileRepository(Path(resolved.DATA_PATH)),
        memory_store=memory_store,
    )


__all__ = [
    "DEFAULT_PROFILE",
    "PROFILE_VERSION",
    "UserProfileService",
    "get_user_context_summary",
    "get_user_profile",
    "get_user_profile_summary",
    "update_user_profile",
]
