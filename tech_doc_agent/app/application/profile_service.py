from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tech_doc_agent.app.application.learning_models import MemoryFragment
from tech_doc_agent.app.application.profile_models import (
    UserProfile,
    UserProfileUpdate,
    UserProfileUpdateResult,
)
from tech_doc_agent.app.core.tenant import TenantContext, parse_tenant


class UserProfileRepositoryPort(Protocol):
    def get(self, tenant: TenantContext) -> UserProfile: ...

    def save(self, profile: UserProfile) -> None: ...


class ProfileMemoryReaderPort(Protocol):
    def query_memories(
        self,
        query: str,
        *,
        user_id: str,
        namespace: str,
        limit: int,
    ) -> Sequence[MemoryFragment]: ...

    def recent_memories(
        self,
        *,
        user_id: str,
        namespace: str,
        limit: int,
    ) -> Sequence[MemoryFragment]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class UserProfileService:
    repository: UserProfileRepositoryPort
    memory_store: ProfileMemoryReaderPort | None = None
    clock: Callable[[], datetime] = _utc_now

    def get_profile(
        self,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> UserProfile:
        tenant = parse_tenant(user_id, namespace)
        return self.repository.get(tenant)

    def update_profile(
        self,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
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
        tenant = parse_tenant(user_id, namespace)
        profile = self.repository.get(tenant)
        update = UserProfileUpdate.create(
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
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("User profile clock must return a timezone-aware datetime.")
        result = profile.apply(update, timestamp=now.isoformat())
        if result.changed:
            self.repository.save(result.profile)
        return result

    def profile_summary(
        self,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> str:
        return format_user_profile_summary(
            self.get_profile(user_id=user_id, namespace=namespace)
        )

    def context_summary(
        self,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
        memory_query: str = "",
        memory_limit: int = 5,
    ) -> str:
        tenant = parse_tenant(user_id, namespace)
        summary = format_user_profile_summary(self.repository.get(tenant))
        memories = self._read_memories(
            tenant,
            query=memory_query,
            limit=memory_limit,
        )
        memory_lines = [
            f"- [{memory.kind}] {memory.topic}：{memory.content}"
            for memory in memories
            if memory.content
        ]
        if not memory_lines:
            return summary
        return summary + "\n长期学习轨迹记忆：\n" + "\n".join(memory_lines)

    def _read_memories(
        self,
        tenant: TenantContext,
        *,
        query: str,
        limit: int,
    ) -> Sequence[MemoryFragment]:
        if self.memory_store is None:
            return ()
        if query:
            return self.memory_store.query_memories(
                query,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
                limit=limit,
            )
        return self.memory_store.recent_memories(
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            limit=limit,
        )


def format_user_profile_summary(profile: UserProfile) -> str:
    summary = (
        f"用户ID：{profile.tenant.user_id}\n"
        f"知识库命名空间：{profile.tenant.namespace}\n"
        "用户学习偏好：\n"
        f"- 经验水平：{profile.experience_level}\n"
        f"- 解释风格：{profile.explanation_style}\n"
        f"- 解释深度：{profile.depth}\n"
        f"- 语言偏好：{profile.language}"
    )
    profile_lines: list[str] = []
    if profile.known_topics:
        profile_lines.append(f"- 已掌握/熟悉主题：{', '.join(profile.known_topics)}")
    if profile.weak_topics:
        profile_lines.append(f"- 仍需巩固主题：{', '.join(profile.weak_topics)}")
    if profile.notes:
        profile_lines.append(f"- 其他画像备注：{profile.notes}")
    if profile_lines:
        summary += "\n长期用户画像：\n" + "\n".join(profile_lines)
    return summary


__all__ = [
    "ProfileMemoryReaderPort",
    "UserProfileRepositoryPort",
    "UserProfileService",
    "format_user_profile_summary",
]
