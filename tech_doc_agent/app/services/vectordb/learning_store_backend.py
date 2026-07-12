"""
LearningStore backend:
- 负责学习记录的查询和 domain model 归一化
- 通过共享 LearningStateUnitOfWork 持久化，不暴露 @tool
- tool schema 与写入用例位于 application/tools 层
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tech_doc_agent.app.application.learning_models import LearningRecord
from tech_doc_agent.app.application.learning_state import LearningStateUnitOfWork
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.core.tenant import TenantContext, parse_tenant
from tech_doc_agent.app.infrastructure.persistence.learning_state_repository import (
    LearningStateSnapshotRepository,
)
from tech_doc_agent.app.services.vectordb.text_match import query_matches


class LearningStore:
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
    def record_models(self) -> tuple[LearningRecord, ...]:
        return self.unit_of_work.records

    @property
    def records(self) -> list[dict[str, Any]]:
        """Compatibility view for external callers that still consume JSON-like rows."""
        return [record.to_payload() for record in self.record_models]

    @records.setter
    def records(
        self,
        value: Sequence[LearningRecord | Mapping[str, Any]],
    ) -> None:
        self.unit_of_work.replace_records(
            [self._normalize_record(record) for record in value]
        )

    def load(self) -> bool:
        return self.unit_of_work.load()

    def save(self) -> bool:
        return self.unit_of_work.save()

    def _make_record(
        self,
        knowledge: str,
        timestamp: str,
        score: float | None,
        tenant: TenantContext | None = None,
        reviewtimes: int = 1,
    ) -> LearningRecord:
        return LearningRecord.create(
            knowledge=knowledge,
            timestamp=timestamp,
            score=score,
            tenant=tenant or parse_tenant(),
            reviewtimes=reviewtimes,
        )

    def _normalize_record(
        self,
        record: LearningRecord | Mapping[str, Any],
        *,
        fallback_tenant: TenantContext | None = None,
    ) -> LearningRecord:
        if isinstance(record, LearningRecord):
            return record
        if not isinstance(record, Mapping):
            raise TypeError("Learning records must be LearningRecord values or mappings.")
        return LearningRecord.from_payload(
            record,
            fallback_tenant=fallback_tenant,
        )

    @staticmethod
    def _record_matches_tenant(
        record: LearningRecord,
        tenant: TenantContext,
    ) -> bool:
        return record.tenant == tenant

    def query_records(
        self,
        query: str,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> list[LearningRecord]:
        tenant = parse_tenant(user_id, namespace)
        return [
            record
            for record in self.record_models
            if self._record_matches_tenant(record, tenant)
            and query_matches(query, record.knowledge)
        ]

    def list_records(
        self,
        *,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> list[LearningRecord]:
        tenant = parse_tenant(user_id, namespace)
        return [
            record
            for record in self.record_models
            if self._record_matches_tenant(record, tenant)
        ]

    def read_by_query(
        self,
        query: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            record.to_payload()
            for record in self.query_records(
                query,
                user_id=user_id,
                namespace=namespace,
            )
        ]

    def read_overview(
        self,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            record.to_payload()
            for record in self.list_records(
                user_id=user_id,
                namespace=namespace,
            )
        ]

    def prepare_upsert_record(
        self,
        records: Sequence[LearningRecord],
        *,
        knowledge: str,
        timestamp: str,
        score: float | None,
        tenant: TenantContext,
    ) -> tuple[list[LearningRecord], str]:
        candidate = list(records)
        index = next(
            (
                position
                for position, record in enumerate(candidate)
                if knowledge == record.knowledge
                and self._record_matches_tenant(record, tenant)
            ),
            -1,
        )
        if index == -1:
            candidate.append(self._make_record(knowledge, timestamp, score, tenant))
            return (
                candidate,
                f"Learning record for '{knowledge}' has been added successfully.",
            )

        candidate[index] = candidate[index].reviewed(
            timestamp=timestamp,
            score=score,
        )
        return (
            candidate,
            f"Learning record for '{knowledge}' has been updated successfully.",
        )

    def upsert_record(
        self,
        knowledge: str,
        timestamp: str,
        score: float | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> str:
        tenant = parse_tenant(user_id, namespace)
        records, message = self.prepare_upsert_record(
            self.record_models,
            knowledge=knowledge,
            timestamp=timestamp,
            score=score,
            tenant=tenant,
        )
        self.unit_of_work.replace_records(records)
        return message
