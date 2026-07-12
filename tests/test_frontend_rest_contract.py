from typing import get_args

from tech_doc_agent.app.api.schemas import (
    HistoryViewItem,
    HistoryViewResponse,
    LearningOverviewResponse,
    LearningRecord,
    SessionStateResponse,
)


EXPECTED_FIELDS = {
    SessionStateResponse: {
        "session_id",
        "user_id",
        "namespace",
        "exists",
        "pending_interrupt",
        "learning_target",
        "message_count",
        "current_agent",
        "workflow_plan",
        "plan_index",
        "budget_usage",
        "budget_status",
        "budget_termination",
    },
    HistoryViewResponse: {
        "session_id",
        "user_id",
        "namespace",
        "learning_target",
        "pending_interrupt",
        "message_count",
        "messages",
    },
    HistoryViewItem: {
        "id",
        "role",
        "kind",
        "content",
        "name",
        "tool_call_id",
    },
    LearningOverviewResponse: {
        "user_id",
        "namespace",
        "total",
        "average_score",
        "needs_review_count",
        "records",
    },
    LearningRecord: {
        "knowledge",
        "timestamp",
        "score",
        "reviewtimes",
        "user_id",
        "namespace",
    },
}


def test_frontend_runtime_decoders_track_backend_response_fields():
    for model, expected in EXPECTED_FIELDS.items():
        assert set(model.model_fields) == expected


def test_history_role_is_an_explicit_cross_language_enum():
    annotation = HistoryViewItem.model_fields["role"].annotation
    assert set(get_args(annotation)) == {"user", "assistant", "system", "tool"}
