"""
LearningStore backend:
- 负责学习记录的查询和领域归一化
- 通过共享 LearningStateUnitOfWork 持久化，不暴露 @tool
- tool schema 与写入用例位于 application/tools 层
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tech_doc_agent.app.application.learning_state import LearningStateUnitOfWork
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.core.tenant import TenantContext, tenant_from_values
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
        self.unit_of_work = unit_of_work or LearningStateUnitOfWork(LearningStateSnapshotRepository(data_path))

    @property
    def records(self) -> list[dict[str, Any]]:
        return self.unit_of_work.records

    @records.setter
    def records(self, value: list[dict[str, Any]]) -> None:
        self.unit_of_work.replace_records(value)

    def load(self) -> bool:
        if not self.unit_of_work.load():
            return False
        self.normalize_records()
        return True

    def save(self) -> bool:
        self.normalize_records()
        return self.unit_of_work.save()

    def _make_record(
        self,
        knowledge: str,
        timestamp: str,
        score: float | None,
        tenant: TenantContext | None = None,
        reviewtimes: int = 1,
    ) -> dict[str, Any]:
        tenant = tenant or tenant_from_values()
        return {
            "knowledge": knowledge,
            "timestamp": timestamp,
            "score": score if score is not None else 0.0,
            "reviewtimes": reviewtimes,
            "user_id": tenant.user_id,
            "namespace": tenant.namespace,
        }

    def normalize_records(self) -> None:
        self.records = [self._normalize_record(record) for record in self.records]

    def _normalize_record(
        self,
        record: dict[str, Any],
        *,
        fallback_tenant: TenantContext | None = None,
    ) -> dict[str, Any]:
        fallback_tenant = fallback_tenant or tenant_from_values()
        tenant = tenant_from_values(
            record.get("user_id") or fallback_tenant.user_id,
            record.get("namespace") or fallback_tenant.namespace,
        )
        normalized = dict(record)
        normalized["score"] = float(normalized.get("score", 0.0) or 0.0)
        normalized["reviewtimes"] = int(normalized.get("reviewtimes", 0) or 0)
        normalized["user_id"] = tenant.user_id
        normalized["namespace"] = tenant.namespace
        return normalized

    def _record_matches_tenant(
        self,
        record: dict[str, Any],
        tenant: TenantContext,
    ) -> bool:
        normalized = self._normalize_record(record)
        return normalized["user_id"] == tenant.user_id and normalized["namespace"] == tenant.namespace

    def read_by_query(
        self,
        query: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        tenant = tenant_from_values(user_id, namespace)
        result = []
        for record in self.records:
            if not self._record_matches_tenant(record, tenant):
                continue
            if query_matches(query, record.get("knowledge", "")):
                result.append(self._normalize_record(record, fallback_tenant=tenant))
        return result

    def read_overview(
        self,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        tenant = tenant_from_values(user_id, namespace)
        return [
            self._normalize_record(record, fallback_tenant=tenant)
            for record in self.records
            if self._record_matches_tenant(record, tenant)
        ]

    def prepare_upsert_record(
        self,
        records: list[dict[str, Any]],
        *,
        knowledge: str,
        timestamp: str,
        score: float | None,
        tenant: TenantContext,
    ) -> tuple[list[dict[str, Any]], str]:
        candidate = [self._normalize_record(record) for record in records]
        index = next(
            (
                position
                for position, record in enumerate(candidate)
                if knowledge == record.get("knowledge") and self._record_matches_tenant(record, tenant)
            ),
            -1,
        )
        if index == -1:
            candidate.append(self._make_record(knowledge, timestamp, score, tenant))
            return (
                candidate,
                f"Learning record for '{knowledge}' has been added successfully.",
            )

        updated = self._normalize_record(
            candidate[index],
            fallback_tenant=tenant,
        )
        updated["timestamp"] = timestamp
        if score is not None:
            updated["score"] = score
        updated["reviewtimes"] += 1
        updated["user_id"] = tenant.user_id
        updated["namespace"] = tenant.namespace
        candidate[index] = updated
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
        tenant = tenant_from_values(user_id, namespace)
        records, message = self.prepare_upsert_record(
            self.records,
            knowledge=knowledge,
            timestamp=timestamp,
            score=score,
            tenant=tenant,
        )
        self.records = records
        return message
