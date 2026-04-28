from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from tech_doc_agent.app.api.schemas import (
    LearningOverviewResponse,
    LearningRecord,
)
from tech_doc_agent.app.services.tools.learning_store import _learning_store


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


def _read_records() -> list[LearningRecord]:
    return [LearningRecord(**record) for record in _learning_store.read_overview()]


@router.get("/learning/overview", response_model=LearningOverviewResponse)
def get_learning_overview():
    records = _read_records()
    total = len(records)
    average_score = sum(record.score for record in records) / total if total else 0.0
    now = datetime.now(timezone.utc)

    return LearningOverviewResponse(
        total=total,
        average_score=average_score,
        needs_review_count=sum(1 for record in records if _needs_review(record, now)),
        records=records,
    )


@router.get("/learning/records", response_model=list[LearningRecord])
def get_learning_records():
    return _read_records()
