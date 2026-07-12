import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tech_doc_agent.app.api.sse.contract import SSE_EVENT_NAMES
from tech_doc_agent.app.api.sse.events import sse_event
from tech_doc_agent.app.api.sse.payloads import (
    SSE_PAYLOAD_MODELS,
    validate_sse_payload,
)
from tech_doc_agent.app.core.observability import trace_context


EXAMPLES_PATH = Path(__file__).resolve().parents[1] / "contracts" / "sse_v1_examples.json"


def _examples() -> dict[str, dict]:
    return json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))


def test_every_sse_event_has_a_runtime_payload_model_and_shared_example():
    examples = _examples()

    assert set(SSE_PAYLOAD_MODELS) == SSE_EVENT_NAMES
    assert set(examples) == SSE_EVENT_NAMES
    for event, payload in examples.items():
        assert validate_sse_payload(event, payload) == payload


def test_sse_event_injects_trace_context_before_payload_validation():
    with trace_context(
        trace_id="trace-1",
        session_id="session-1",
        user_id="user-a",
        namespace="docs-a",
    ):
        event = sse_event("done", {})

    assert event.data == {
        "trace_id": "trace-1",
        "session_id": "session-1",
        "user_id": "user-a",
        "namespace": "docs-a",
    }


def test_known_event_rejects_missing_wrong_or_extra_fields():
    with pytest.raises(ValidationError):
        validate_sse_payload("token", {"agent": "primary"})
    with pytest.raises(ValidationError):
        validate_sse_payload("token", {"text": 42})
    with pytest.raises(ValidationError):
        validate_sse_payload("done", {"session_id": "session-1", "surprise": True})

    incomplete_snapshot = _examples()["session_snapshot"]
    incomplete_snapshot.pop("user_id")
    with pytest.raises(ValidationError):
        validate_sse_payload("session_snapshot", incomplete_snapshot)


def test_unknown_backend_event_is_a_programmer_error():
    with pytest.raises(ValueError, match="Unsupported SSE event"):
        validate_sse_payload("future_event", {})


def test_plan_update_requires_at_least_one_semantic_field():
    with pytest.raises(ValidationError, match="at least one update field"):
        validate_sse_payload("plan_update", {})
