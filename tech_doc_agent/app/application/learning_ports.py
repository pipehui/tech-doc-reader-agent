from collections.abc import Sequence
from typing import Protocol

from tech_doc_agent.app.application.learning_commands import (
    UpdateLearningStateCommand,
    UpdateLearningStateResult,
)
from tech_doc_agent.app.application.learning_models import LearningRecord, MemoryFragment
from tech_doc_agent.app.core.tenant import TenantContext


class LearningRecordReaderPort(Protocol):
    def query_records(
        self,
        query: str,
        *,
        user_id: str,
        namespace: str,
    ) -> Sequence[LearningRecord]: ...

    def list_records(
        self,
        *,
        user_id: str,
        namespace: str,
    ) -> Sequence[LearningRecord]: ...


class MemoryReaderPort(Protocol):
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


class LearningStateCommandPort(Protocol):
    def update(
        self,
        command: UpdateLearningStateCommand,
    ) -> UpdateLearningStateResult: ...


class LearningRecordUpdaterPort(Protocol):
    def prepare_upsert_record(
        self,
        records: Sequence[LearningRecord],
        *,
        knowledge: str,
        timestamp: str,
        score: float | None,
        tenant: TenantContext,
    ) -> tuple[list[LearningRecord], str]: ...


class MemoryUpdaterPort(Protocol):
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
    ) -> tuple[list[MemoryFragment], MemoryFragment]: ...


__all__ = [
    "LearningRecordReaderPort",
    "LearningRecordUpdaterPort",
    "LearningStateCommandPort",
    "MemoryReaderPort",
    "MemoryUpdaterPort",
]
