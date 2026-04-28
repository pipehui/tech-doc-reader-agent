import pytest
from pydantic import ValidationError

from tech_doc_agent.app.api.schemas import ApproveRequest, ChatRequest


def test_chat_request_accepts_supported_session_and_trace_ids():
    request = ChatRequest(
        session_id="eval:session_1.2-3",
        trace_id="trace-codex_20260428",
        message="hello",
    )

    assert request.session_id == "eval:session_1.2-3"


def test_chat_request_rejects_empty_or_too_long_message():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="session-1", message="")

    with pytest.raises(ValidationError):
        ChatRequest(session_id="session-1", message="x" * 8001)


def test_chat_request_rejects_unsafe_session_id_characters():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="../secret", message="hello")

    with pytest.raises(ValidationError):
        ChatRequest(session_id="session with spaces", message="hello")


def test_approve_request_limits_feedback_length_and_trace_id():
    ApproveRequest(
        session_id="session-1",
        approved=False,
        feedback="reason",
        trace_id="trace-1",
    )

    with pytest.raises(ValidationError):
        ApproveRequest(session_id="session-1", approved=False, feedback="x" * 2001)

    with pytest.raises(ValidationError):
        ApproveRequest(session_id="session-1", approved=True, trace_id="")
