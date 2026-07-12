from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tech_doc_agent.app.application.learning_state import LearningStateUnitOfWork
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.core.tenant import (
    TenantContext,
    normalize_tenant,
    parse_tenant,
)
from tech_doc_agent.app.infrastructure.persistence.learning_state_repository import (
    LearningStateSnapshotRepository,
)
from tech_doc_agent.app.services.vectordb.text_match import query_matches


DEFAULT_MEMORY_KIND = "learned"
MEMORY_KINDS = {"learned", "stuck_point", "misconception", "review_hint"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class MemoryStore:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        unit_of_work: LearningStateUnitOfWork | None = None,
    ) -> None:
        settings = settings or get_settings()
        data_path = Path(settings.DATA_PATH)
        self.store_dir = data_path / "learning_state"
        self.unit_of_work = unit_of_work or LearningStateUnitOfWork(LearningStateSnapshotRepository(data_path))

    @property
    def memories(self) -> list[dict[str, Any]]:
        return self.unit_of_work.memories

    @memories.setter
    def memories(self, value: list[dict[str, Any]]) -> None:
        self.unit_of_work.replace_memories(value)

    def load(self) -> bool:
        if not self.unit_of_work.load():
            return False
        self.normalize_memories()
        return True

    def save(self) -> bool:
        self.normalize_memories()
        return self.unit_of_work.save()

    def normalize_memories(self) -> None:
        self.memories = [self._normalize_memory(memory) for memory in self.memories]

    def _normalize_memory(
        self,
        memory: dict[str, Any],
        *,
        fallback_tenant: TenantContext | None = None,
    ) -> dict[str, Any]:
        fallback_tenant = fallback_tenant or normalize_tenant()
        tenant = normalize_tenant(
            memory.get("user_id") or fallback_tenant.user_id,
            memory.get("namespace") or fallback_tenant.namespace,
        )
        created_at = str(memory.get("created_at") or memory.get("updated_at") or _utc_now())
        updated_at = str(memory.get("updated_at") or created_at)
        kind = str(memory.get("kind") or DEFAULT_MEMORY_KIND).strip()
        if kind not in MEMORY_KINDS:
            kind = DEFAULT_MEMORY_KIND

        confidence = memory.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5

        return {
            "id": str(memory.get("id") or uuid4().hex),
            "user_id": tenant.user_id,
            "namespace": tenant.namespace,
            "kind": kind,
            "topic": str(memory.get("topic") or "").strip(),
            "content": str(memory.get("content") or "").strip(),
            "confidence": max(0.0, min(confidence, 1.0)),
            "source_session_id": _string_or_none(memory.get("source_session_id")),
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _matches_tenant(
        self,
        memory: dict[str, Any],
        tenant: TenantContext,
    ) -> bool:
        normalized = self._normalize_memory(memory)
        return normalized["user_id"] == tenant.user_id and normalized["namespace"] == tenant.namespace

    def prepare_upsert_memory(
        self,
        memories: list[dict[str, Any]],
        *,
        kind: str,
        topic: str,
        content: str,
        confidence: float | None,
        source_session_id: str,
        tenant: TenantContext,
        timestamp: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        candidate = [self._normalize_memory(memory) for memory in memories]
        incoming = self._normalize_memory(
            {
                "kind": kind,
                "topic": topic,
                "content": content,
                "confidence": confidence if confidence is not None else 0.7,
                "source_session_id": source_session_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            fallback_tenant=tenant,
        )

        index = self._find_memory_index(candidate, incoming, tenant)
        if index == -1:
            candidate.append(incoming)
            return candidate, incoming

        existing = self._normalize_memory(
            candidate[index],
            fallback_tenant=tenant,
        )
        existing.update(
            {
                "kind": incoming["kind"],
                "topic": incoming["topic"],
                "content": incoming["content"],
                "confidence": incoming["confidence"],
                "source_session_id": incoming["source_session_id"] or existing.get("source_session_id"),
                "updated_at": timestamp,
            }
        )
        candidate[index] = existing
        return candidate, existing

    def upsert_memory(
        self,
        *,
        kind: str,
        topic: str,
        content: str,
        confidence: float | None = None,
        source_session_id: str | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        tenant = parse_tenant(user_id, namespace)
        memories, memory = self.prepare_upsert_memory(
            self.memories,
            kind=kind,
            topic=topic,
            content=content,
            confidence=confidence,
            source_session_id=source_session_id or "",
            tenant=tenant,
            timestamp=timestamp or _utc_now(),
        )
        self.memories = memories
        return memory

    def _find_memory_index(
        self,
        memories: list[dict[str, Any]],
        incoming: dict[str, Any],
        tenant: TenantContext,
    ) -> int:
        for index, memory in enumerate(memories):
            normalized = self._normalize_memory(
                memory,
                fallback_tenant=tenant,
            )
            if normalized["user_id"] != tenant.user_id or normalized["namespace"] != tenant.namespace:
                continue
            if normalized["kind"] == incoming["kind"] and normalized["topic"] == incoming["topic"]:
                return index
        return -1

    def read_by_query(
        self,
        query: str = "",
        *,
        user_id: str | None = None,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        tenant = parse_tenant(user_id, namespace)
        matched = []

        for memory in self.memories:
            if not self._matches_tenant(memory, tenant):
                continue
            normalized = self._normalize_memory(
                memory,
                fallback_tenant=tenant,
            )
            if not query_matches(
                query,
                normalized["kind"],
                normalized["topic"],
                normalized["content"],
            ):
                continue
            matched.append(normalized)

        return sorted(
            matched,
            key=lambda item: item["updated_at"],
            reverse=True,
        )[: max(1, limit)]

    def read_recent(
        self,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return self.read_by_query(
            "",
            user_id=user_id,
            namespace=namespace,
            limit=limit,
        )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
