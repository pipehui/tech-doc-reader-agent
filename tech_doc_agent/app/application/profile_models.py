from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from tech_doc_agent.app.core.tenant import TenantContext


PROFILE_VERSION = 1
DEFAULT_EXPERIENCE_LEVEL = "初学者"
DEFAULT_EXPLANATION_STYLE = "先讲原理再看代码"
DEFAULT_DEPTH = "详细，多举例"
DEFAULT_LANGUAGE = "中文为主，技术术语保留英文"
ProfileUpdateStatus = Literal["updated", "unchanged"]


@dataclass(frozen=True, slots=True)
class UserProfileUpdate:
    experience_level: str | None = None
    explanation_style: str | None = None
    depth: str | None = None
    language: str | None = None
    known_topics: tuple[str, ...] = ()
    weak_topics: tuple[str, ...] = ()
    resolved_weak_topics: tuple[str, ...] = ()
    notes: str | None = None
    evidence: str | None = None

    @classmethod
    def create(
        cls,
        *,
        experience_level: str | None = None,
        explanation_style: str | None = None,
        depth: str | None = None,
        language: str | None = None,
        known_topics: Sequence[str] | None = None,
        weak_topics: Sequence[str] | None = None,
        resolved_weak_topics: Sequence[str] | None = None,
        notes: str | None = None,
        evidence: str | None = None,
    ) -> UserProfileUpdate:
        return cls(
            experience_level=_string_or_none(experience_level),
            explanation_style=_string_or_none(explanation_style),
            depth=_string_or_none(depth),
            language=_string_or_none(language),
            known_topics=_merge_topics((), known_topics or ()),
            weak_topics=_merge_topics((), weak_topics or ()),
            resolved_weak_topics=_merge_topics((), resolved_weak_topics or ()),
            notes=_string_or_none(notes),
            evidence=_string_or_none(evidence),
        )


@dataclass(frozen=True, slots=True)
class UserProfile:
    tenant: TenantContext
    experience_level: str = DEFAULT_EXPERIENCE_LEVEL
    explanation_style: str = DEFAULT_EXPLANATION_STYLE
    depth: str = DEFAULT_DEPTH
    language: str = DEFAULT_LANGUAGE
    known_topics: tuple[str, ...] = ()
    weak_topics: tuple[str, ...] = ()
    notes: str = ""
    last_update_reason: str | None = None
    updated_at: str | None = None
    profile_version: int = PROFILE_VERSION

    @classmethod
    def default(cls, tenant: TenantContext) -> UserProfile:
        return cls(tenant=tenant)

    @classmethod
    def from_payload(
        cls,
        value: Mapping[str, Any],
        *,
        tenant: TenantContext,
    ) -> UserProfile:
        return cls(
            tenant=tenant,
            experience_level=(
                _string_or_none(value.get("experience_level"))
                or DEFAULT_EXPERIENCE_LEVEL
            ),
            explanation_style=(
                _string_or_none(value.get("explanation_style"))
                or DEFAULT_EXPLANATION_STYLE
            ),
            depth=_string_or_none(value.get("depth")) or DEFAULT_DEPTH,
            language=_string_or_none(value.get("language")) or DEFAULT_LANGUAGE,
            known_topics=_topics_from_payload(value.get("known_topics")),
            weak_topics=_topics_from_payload(value.get("weak_topics")),
            notes=_string_or_empty(value.get("notes")),
            last_update_reason=_string_or_none(value.get("last_update_reason")),
            updated_at=_string_or_none(value.get("updated_at")),
            profile_version=_positive_int(
                value.get("profile_version"),
                default=PROFILE_VERSION,
            ),
        )

    def apply(
        self,
        update: UserProfileUpdate,
        *,
        timestamp: str,
    ) -> UserProfileUpdateResult:
        experience_level = update.experience_level or self.experience_level
        explanation_style = update.explanation_style or self.explanation_style
        depth = update.depth or self.depth
        language = update.language or self.language
        notes = update.notes or self.notes
        changed = (
            experience_level,
            explanation_style,
            depth,
            language,
            notes,
        ) != (
            self.experience_level,
            self.explanation_style,
            self.depth,
            self.language,
            self.notes,
        )

        known_topics = _merge_topics(self.known_topics, update.known_topics)
        if known_topics != self.known_topics:
            changed = True

        merged_weak_topics = _merge_topics(self.weak_topics, update.weak_topics)
        resolved_keys = {_topic_key(topic) for topic in update.resolved_weak_topics}
        known_keys = {_topic_key(topic) for topic in known_topics}
        weak_topics = tuple(
            topic
            for topic in merged_weak_topics
            if _topic_key(topic) not in resolved_keys
            and _topic_key(topic) not in known_keys
        )
        if weak_topics != self.weak_topics:
            changed = True

        last_update_reason = self.last_update_reason
        if update.evidence is not None:
            last_update_reason = update.evidence
            changed = True

        if not changed:
            return UserProfileUpdateResult(profile=self, status="unchanged")

        updated = replace(
            self,
            experience_level=experience_level,
            explanation_style=explanation_style,
            depth=depth,
            language=language,
            known_topics=known_topics,
            weak_topics=weak_topics,
            notes=notes,
            last_update_reason=last_update_reason,
            updated_at=str(timestamp).strip(),
        )
        return UserProfileUpdateResult(profile=updated, status="updated")

    def to_payload(self) -> dict[str, Any]:
        return {
            "profile_version": self.profile_version,
            "user_id": self.tenant.user_id,
            "namespace": self.tenant.namespace,
            "experience_level": self.experience_level,
            "explanation_style": self.explanation_style,
            "depth": self.depth,
            "language": self.language,
            "known_topics": list(self.known_topics),
            "weak_topics": list(self.weak_topics),
            "notes": self.notes,
            "last_update_reason": self.last_update_reason,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class UserProfileUpdateResult:
    profile: UserProfile
    status: ProfileUpdateStatus

    @property
    def changed(self) -> bool:
        return self.status == "updated"

    def to_payload(self) -> dict[str, Any]:
        return {**self.profile.to_payload(), "status": self.status}


def _topics_from_payload(value: Any) -> tuple[str, ...]:
    return _merge_topics((), value if isinstance(value, list) else ())


def _merge_topics(
    existing: Sequence[str],
    incoming: Sequence[Any],
) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in (*existing, *incoming):
        text = _string_or_empty(item)
        if not text:
            continue
        key = _topic_key(text)
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return tuple(merged)


def _topic_key(topic: str) -> str:
    return topic.strip().casefold()


def _positive_int(value: Any, *, default: int) -> int:
    try:
        normalized = int(value or default)
    except (TypeError, ValueError, OverflowError):
        return default
    return normalized if normalized > 0 else default


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _string_or_none(value: Any) -> str | None:
    normalized = _string_or_empty(value)
    return normalized or None


__all__ = [
    "DEFAULT_DEPTH",
    "DEFAULT_EXPERIENCE_LEVEL",
    "DEFAULT_EXPLANATION_STYLE",
    "DEFAULT_LANGUAGE",
    "PROFILE_VERSION",
    "ProfileUpdateStatus",
    "UserProfile",
    "UserProfileUpdate",
    "UserProfileUpdateResult",
]
