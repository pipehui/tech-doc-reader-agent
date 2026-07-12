from datetime import datetime, timedelta, timezone
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, status

from tech_doc_agent.app.api.schemas import (
    LearningMemoryResponse,
    LearningOverviewResponse,
    LearningRecord,
    MemoryRecord,
    UserProfileResponse,
)
from tech_doc_agent.app.api.tenant import resolve_request_tenant
from tech_doc_agent.app.application.learning_ports import (
    LearningRecordReaderPort,
    MemoryReaderPort,
)
from tech_doc_agent.app.application.profile_service import UserProfileServicePort
from tech_doc_agent.app.core.tenant import TenantContext


router = APIRouter()
REVIEW_SCORE_THRESHOLD = 0.6
REVIEW_AGE = timedelta(days=14)


class LearningApiResources(Protocol):
    @property
    def learning_store(self) -> LearningRecordReaderPort: ...

    @property
    def memory_store(self) -> MemoryReaderPort: ...

    @property
    def profile_service(self) -> UserProfileServicePort: ...


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


def _runtime_resources(request: Request) -> LearningApiResources:
    runtime = getattr(request.app.state, "runtime", None)
    resources = getattr(runtime, "resources", None)
    if resources is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application resources are not initialized.",
        )
    return cast(LearningApiResources, resources)


def _read_records(
    resources: LearningApiResources,
    tenant: TenantContext,
) -> list[LearningRecord]:
    return [
        LearningRecord(**record.to_payload())
        for record in resources.learning_store.list_records(
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
    tenant = resolve_request_tenant(request, user_id, namespace)
    records = _read_records(_runtime_resources(request), tenant)
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
    tenant = resolve_request_tenant(request, user_id, namespace)
    return _read_records(_runtime_resources(request), tenant)


@router.get("/learning/memory", response_model=LearningMemoryResponse)
def get_learning_memory(
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
    query: str = "",
    limit: int = 20,
):
    tenant = resolve_request_tenant(request, user_id, namespace)
    resources = _runtime_resources(request)
    memories = [
        MemoryRecord(**memory.to_payload())
        for memory in resources.memory_store.query_memories(
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


@router.get("/learning/profile", response_model=UserProfileResponse)
def get_learning_profile(
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
):
    tenant = resolve_request_tenant(request, user_id, namespace)
    resources = _runtime_resources(request)
    profile = resources.profile_service.get_profile(
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    return UserProfileResponse(
        **profile.to_payload()
    )
