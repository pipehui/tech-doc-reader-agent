from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Literal, cast
from uuid import uuid4

from tech_doc_agent.app.core.tenant import TenantContext, normalize_tenant


MemoryKind = Literal["learned", "stuck_point", "misconception", "review_hint"]
DEFAULT_MEMORY_KIND: MemoryKind = "learned"
MEMORY_KINDS: frozenset[MemoryKind] = frozenset(
    {"learned", "stuck_point", "misconception", "review_hint"}
)


@dataclass(frozen=True, slots=True)
class LearningRecord:
    knowledge: str
    timestamp: str
    score: float
    reviewtimes: int
    tenant: TenantContext

    @classmethod
    def create(
        cls,
        *,
        knowledge: str,
        timestamp: str,
        score: float | None,
        tenant: TenantContext,
        reviewtimes: int = 1,
    ) -> LearningRecord:
        return cls(
            knowledge=str(knowledge).strip(),
            timestamp=str(timestamp).strip(),
            score=_finite_float(score, default=0.0),
            reviewtimes=_non_negative_int(reviewtimes),
            tenant=tenant,
        )

    @classmethod
    def from_payload(
        cls,
        value: Mapping[str, Any],
        *,
        fallback_tenant: TenantContext | None = None,
    ) -> LearningRecord:
        fallback = fallback_tenant or normalize_tenant()
        tenant = normalize_tenant(
            value.get("user_id") or fallback.user_id,
            value.get("namespace") or fallback.namespace,
        )
        return cls.create(
            knowledge=str(value.get("knowledge") or ""),
            timestamp=str(value.get("timestamp") or ""),
            score=_finite_float(value.get("score"), default=0.0),
            reviewtimes=_non_negative_int(value.get("reviewtimes")),
            tenant=tenant,
        )

    def reviewed(self, *, timestamp: str, score: float | None) -> LearningRecord:
        return replace(
            self,
            timestamp=str(timestamp).strip(),
            score=self.score if score is None else _finite_float(score, default=self.score),
            reviewtimes=self.reviewtimes + 1,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "knowledge": self.knowledge,
            "timestamp": self.timestamp,
            "score": self.score,
            "reviewtimes": self.reviewtimes,
            "user_id": self.tenant.user_id,
            "namespace": self.tenant.namespace,
        }


@dataclass(frozen=True, slots=True)
class MemoryFragment:
    id: str
    tenant: TenantContext
    kind: MemoryKind
    topic: str
    content: str
    confidence: float
    source_session_id: str | None
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        topic: str,
        content: str,
        confidence: float | None,
        source_session_id: str | None,
        tenant: TenantContext,
        timestamp: str,
        memory_id: str | None = None,
    ) -> MemoryFragment:
        normalized_kind = _memory_kind(kind)
        normalized_timestamp = str(timestamp).strip()
        return cls(
            id=str(memory_id or uuid4().hex),
            tenant=tenant,
            kind=normalized_kind,
            topic=str(topic).strip(),
            content=str(content).strip(),
            confidence=_bounded_confidence(confidence, default=0.7),
            source_session_id=_string_or_none(source_session_id),
            created_at=normalized_timestamp,
            updated_at=normalized_timestamp,
        )

    @classmethod
    def from_payload(
        cls,
        value: Mapping[str, Any],
        *,
        fallback_tenant: TenantContext | None = None,
        now: str | None = None,
    ) -> MemoryFragment:
        fallback = fallback_tenant or normalize_tenant()
        tenant = normalize_tenant(
            value.get("user_id") or fallback.user_id,
            value.get("namespace") or fallback.namespace,
        )
        current_time = now or datetime.now(UTC).isoformat()
        created_at = str(value.get("created_at") or value.get("updated_at") or current_time)
        updated_at = str(value.get("updated_at") or created_at)
        return cls(
            id=str(value.get("id") or uuid4().hex),
            tenant=tenant,
            kind=_memory_kind(value.get("kind")),
            topic=str(value.get("topic") or "").strip(),
            content=str(value.get("content") or "").strip(),
            confidence=_bounded_confidence(value.get("confidence"), default=0.5),
            source_session_id=_string_or_none(value.get("source_session_id")),
            created_at=created_at,
            updated_at=updated_at,
        )

    def updated_from(
        self,
        incoming: MemoryFragment,
        *,
        timestamp: str,
    ) -> MemoryFragment:
        return replace(
            self,
            kind=incoming.kind,
            topic=incoming.topic,
            content=incoming.content,
            confidence=incoming.confidence,
            source_session_id=incoming.source_session_id or self.source_session_id,
            updated_at=str(timestamp),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.tenant.user_id,
            "namespace": self.tenant.namespace,
            "kind": self.kind,
            "topic": self.topic,
            "content": self.content,
            "confidence": self.confidence,
            "source_session_id": self.source_session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _memory_kind(value: Any) -> MemoryKind:
    normalized = str(value or DEFAULT_MEMORY_KIND).strip()
    if normalized not in MEMORY_KINDS:
        return DEFAULT_MEMORY_KIND
    return cast(MemoryKind, normalized)


def _finite_float(value: Any, *, default: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return default
    return normalized if isfinite(normalized) else default


def _bounded_confidence(value: Any, *, default: float) -> float:
    normalized = _finite_float(value, default=default)
    return max(0.0, min(normalized, 1.0))


def _non_negative_int(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, normalized)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


__all__ = [
    "DEFAULT_MEMORY_KIND",
    "MEMORY_KINDS",
    "LearningRecord",
    "MemoryFragment",
    "MemoryKind",
]
