from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tech_doc_agent.app.application.learning_models import (
    DEFAULT_MEMORY_KIND,
    MEMORY_KINDS,
    MemoryFragment,
)
from tech_doc_agent.app.application.learning_unit_of_work import LearningStateUnitOfWork
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.core.tenant import TenantContext, parse_tenant
from tech_doc_agent.app.infrastructure.persistence.learning_state_repository import (
    LearningStateSnapshotRepository,
)

from .text_match import query_matches


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
        self.unit_of_work = unit_of_work or LearningStateUnitOfWork(
            LearningStateSnapshotRepository(data_path)
        )

    @property
    def memory_models(self) -> tuple[MemoryFragment, ...]:
        return self.unit_of_work.memories

    @property
    def memories(self) -> list[dict[str, Any]]:
        """Compatibility view for external callers that still consume JSON-like rows."""
        return [memory.to_payload() for memory in self.memory_models]

    @memories.setter
    def memories(
        self,
        value: Sequence[MemoryFragment | Mapping[str, Any]],
    ) -> None:
        self.unit_of_work.replace_memories(
            [self._normalize_memory(memory) for memory in value]
        )

    def load(self) -> bool:
        return self.unit_of_work.load()

    def save(self) -> bool:
        return self.unit_of_work.save()

    def _normalize_memory(
        self,
        memory: MemoryFragment | Mapping[str, Any],
        *,
        fallback_tenant: TenantContext | None = None,
    ) -> MemoryFragment:
        if isinstance(memory, MemoryFragment):
            return memory
        if not isinstance(memory, Mapping):
            raise TypeError("Memories must be MemoryFragment values or mappings.")
        return MemoryFragment.from_payload(
            memory,
            fallback_tenant=fallback_tenant,
        )

    @staticmethod
    def _matches_tenant(
        memory: MemoryFragment,
        tenant: TenantContext,
    ) -> bool:
        return memory.tenant == tenant

    def prepare_upsert_memory(
        self,
        memories: Sequence[MemoryFragment],
        *,
        kind: str,
        topic: str,
        content: str,
        confidence: float | None,
        source_session_id: str,
        tenant: TenantContext,
        timestamp: str,
    ) -> tuple[list[MemoryFragment], MemoryFragment]:
        candidate = list(memories)
        incoming = MemoryFragment.create(
            kind=kind,
            topic=topic,
            content=content,
            confidence=confidence if confidence is not None else 0.7,
            source_session_id=source_session_id,
            tenant=tenant,
            timestamp=timestamp,
        )

        index = self._find_memory_index(candidate, incoming, tenant)
        if index == -1:
            candidate.append(incoming)
            return candidate, incoming

        updated = candidate[index].updated_from(
            incoming,
            timestamp=timestamp,
        )
        candidate[index] = updated
        return candidate, updated

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
            self.memory_models,
            kind=kind,
            topic=topic,
            content=content,
            confidence=confidence,
            source_session_id=source_session_id or "",
            tenant=tenant,
            timestamp=timestamp or _utc_now(),
        )
        self.unit_of_work.replace_memories(memories)
        return memory.to_payload()

    @staticmethod
    def _find_memory_index(
        memories: Sequence[MemoryFragment],
        incoming: MemoryFragment,
        tenant: TenantContext,
    ) -> int:
        for index, memory in enumerate(memories):
            if memory.tenant != tenant:
                continue
            if memory.kind == incoming.kind and memory.topic == incoming.topic:
                return index
        return -1

    def query_memories(
        self,
        query: str = "",
        *,
        user_id: str | None = None,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[MemoryFragment]:
        tenant = parse_tenant(user_id, namespace)
        matched = [
            memory
            for memory in self.memory_models
            if self._matches_tenant(memory, tenant)
            and query_matches(
                query,
                memory.kind,
                memory.topic,
                memory.content,
            )
        ]
        return sorted(
            matched,
            key=lambda item: item.updated_at,
            reverse=True,
        )[: max(1, limit)]

    def recent_memories(
        self,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[MemoryFragment]:
        return self.query_memories(
            "",
            user_id=user_id,
            namespace=namespace,
            limit=limit,
        )

    def read_by_query(
        self,
        query: str = "",
        *,
        user_id: str | None = None,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return [
            memory.to_payload()
            for memory in self.query_memories(
                query,
                user_id=user_id,
                namespace=namespace,
                limit=limit,
            )
        ]

    def read_recent(
        self,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return [
            memory.to_payload()
            for memory in self.recent_memories(
                user_id=user_id,
                namespace=namespace,
                limit=limit,
            )
        ]


__all__ = [
    "DEFAULT_MEMORY_KIND",
    "MEMORY_KINDS",
    "MemoryStore",
]
