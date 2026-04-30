from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

from tech_doc_agent.app.core.tenant import TenantContext, tenant_from_values
from tech_doc_agent.app.api.schemas import (
    LearningMemoryResponse,
    LearningOverviewResponse,
    LearningRecord,
    MemoryRecord,
)
from tech_doc_agent.app.services.tools.learning_store import get_learning_store, get_memory_store


router = APIRouter()
REVIEW_SCORE_THRESHOLD = 0.6
REVIEW_AGE = timedelta(days=14)


def _parse_timestamp(timestamp: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _needs_review(record: LearningRecord, now: datetime) -> bool:
    if record.score < REVIEW_SCORE_THRESHOLD:
        return True

    parsed_timestamp = _parse_timestamp(record.timestamp)
    if parsed_timestamp is None:
        return False

    return now - parsed_timestamp > REVIEW_AGE


def _resolve_tenant(
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
) -> TenantContext:
    return tenant_from_values(
        user_id or request.headers.get("x-user-id"),
        namespace or request.headers.get("x-namespace"),
    )


def _read_records(tenant: TenantContext) -> list[LearningRecord]:
    return [
        LearningRecord(**record)
        for record in get_learning_store().read_overview(
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
    ]


@router.get("/learning/overview", response_model=LearningOverviewResponse)
def get_learning_overview(
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
):
    tenant = _resolve_tenant(request, user_id, namespace)
    records = _read_records(tenant)
    total = len(records)
    average_score = sum(record.score for record in records) / total if total else 0.0
    now = datetime.now(timezone.utc)

    return LearningOverviewResponse(
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        total=total,
        average_score=average_score,
        needs_review_count=sum(1 for record in records if _needs_review(record, now)),
        records=records,
    )


@router.get("/learning/records", response_model=list[LearningRecord])
def get_learning_records(
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
):
    tenant = _resolve_tenant(request, user_id, namespace)
    return _read_records(tenant)


@router.get("/learning/memory", response_model=LearningMemoryResponse)
def get_learning_memory(
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
    query: str = "",
    limit: int = 20,
):
    tenant = _resolve_tenant(request, user_id, namespace)
    memories = [
        MemoryRecord(**memory)
        for memory in get_memory_store().read_by_query(
            query,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            limit=limit,
        )
    ]
    return LearningMemoryResponse(
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        total=len(memories),
        memories=memories,
    )
