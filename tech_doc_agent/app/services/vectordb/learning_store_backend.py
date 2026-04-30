"""
LearningStore backend:
- 负责学习记录的本地持久化
- 不暴露 @tool
- tool 层在 learning_store.py
"""
import json
from pathlib import Path
from typing import Any

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.core.tenant import TenantContext, tenant_from_values
from tech_doc_agent.app.services.vectordb.text_match import query_matches

class LearningStore:
    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.store_dir = Path(settings.DATA_PATH) / "learning_store"
        self.records_path = self.store_dir / "records.json"
        self.records: list[dict[str, Any]] = []

    def load(self) -> bool:
        if not self.records_path.exists():
            return False
        with open(self.records_path, "r", encoding="utf-8") as f:
            self.records = json.load(f)
        self.normalize_records()
        return True
    
    def save(self) -> bool:
        self.normalize_records()
        self.store_dir.mkdir(parents=True, exist_ok=True)
        with open(self.records_path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=2)
        return True
    
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

    def _record_matches_tenant(self, record: dict[str, Any], tenant: TenantContext) -> bool:
        normalized = self._normalize_record(record)
        return normalized["user_id"] == tenant.user_id and normalized["namespace"] == tenant.namespace

    def read_by_query(
        self,
        query: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        tenant = tenant_from_values(user_id, namespace)
        res = []
        for record in self.records:
            if not self._record_matches_tenant(record, tenant):
                continue
            if query_matches(query, record.get("knowledge", "")):
                res.append(self._normalize_record(record, fallback_tenant=tenant))

        return res

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
    
    def upsert_record(
        self,
        knowledge: str,
        timestamp: str,
        score: float | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> str:
        tenant = tenant_from_values(user_id, namespace)
        idx = -1
        for i, record in enumerate(self.records):
            if knowledge == record["knowledge"] and self._record_matches_tenant(record, tenant):
                idx = i
                break
        if idx == -1:
            self.records.append(self._make_record(knowledge, timestamp, score, tenant))
            return f"Learning record for '{knowledge}' has been added successfully."

        updated = self._normalize_record(self.records[idx], fallback_tenant=tenant)
        updated["timestamp"] = timestamp
        if score is not None:
            updated["score"] = score
        updated["reviewtimes"] += 1
        updated["user_id"] = tenant.user_id
        updated["namespace"] = tenant.namespace
        self.records[idx] = updated
        return f"Learning record for '{knowledge}' has been updated successfully."
