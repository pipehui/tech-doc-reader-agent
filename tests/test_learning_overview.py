from tech_doc_agent.app.api.routes.learning import _needs_review
from tech_doc_agent.app.api.schemas import LearningRecord


def test_needs_review_when_score_is_low():
    record = LearningRecord(
        knowledge="LangGraph StateGraph",
        timestamp="2026-04-28T00:00:00Z",
        score=0.4,
        reviewtimes=1,
    )

    assert _needs_review(record, now=record_timestamp("2026-04-28T00:00:00Z"))


def test_needs_review_when_record_is_old():
    record = LearningRecord(
        knowledge="FastAPI Depends",
        timestamp="2026-04-01T00:00:00Z",
        score=0.9,
        reviewtimes=1,
    )

    assert _needs_review(record, now=record_timestamp("2026-04-28T00:00:00Z"))


def record_timestamp(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))
