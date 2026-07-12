import json
import logging

import pytest
from pydantic import SecretStr

from tech_doc_agent.app.api.routes.chat import sse_event
from tech_doc_agent.app.core import observability
from tech_doc_agent.app.core.observability import (
    get_trace_context,
    log_event,
    new_trace_id,
    timed_node,
    trace_context,
)


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_trace_context_is_scoped():
    trace_id = new_trace_id()

    with trace_context(trace_id=trace_id, session_id="session-1"):
        assert get_trace_context()["trace_id"] == trace_id
        assert get_trace_context()["session_id"] == "session-1"

    assert get_trace_context() == {}


def test_sse_event_includes_trace_context():
    with trace_context(trace_id="trace-test", session_id="session-1"):
        event = sse_event("token", {"text": "hello"})

    assert event.data["trace_id"] == "trace-test"
    assert event.data["session_id"] == "session-1"


def test_log_event_outputs_structured_json():
    handler = ListHandler()
    observability._LOGGER.addHandler(handler)

    try:
        with trace_context(trace_id="trace-test", session_id="session-1"):
            log_event("unit.test", value={"ok": True})
    finally:
        observability._LOGGER.removeHandler(handler)

    payload = json.loads(handler.records[-1].message)
    assert payload["event"] == "unit.test"
    assert payload["trace_id"] == "trace-test"
    assert payload["session_id"] == "session-1"
    assert payload["value"] == {"ok": True}


def test_log_event_applies_shared_redaction_and_keyed_user_pseudonym(monkeypatch):
    class RedactionSettings:
        TELEMETRY_PSEUDONYM_KEY = SecretStr("controlled-key-with-32-random-bytes")

    monkeypatch.setattr(observability, "get_settings", lambda: RedactionSettings())
    handler = ListHandler()
    observability._LOGGER.addHandler(handler)

    try:
        with trace_context(
            user_id="person@example.com",
            session_id="550e8400-e29b-41d4-a716-446655440000",
        ):
            log_event(
                "unit.redaction",
                authorization="Bearer private-token",
                note="call 13800138000",
            )
    finally:
        observability._LOGGER.removeHandler(handler)

    payload = json.loads(handler.records[-1].message)
    assert payload["user_id"].startswith("pseudonym:")
    assert payload["session_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert payload["authorization"] == "[REDACTED:AUTHORIZATION]"
    assert payload["note"] == "call [REDACTED:PHONE]"
    assert "private-token" not in handler.records[-1].message


def test_log_event_redacts_string_fallback_for_non_json_objects():
    class SensitiveObject:
        def __str__(self):
            return "api_key=private-object-value"

    handler = ListHandler()
    observability._LOGGER.addHandler(handler)
    try:
        log_event("unit.object", value=SensitiveObject())
    finally:
        observability._LOGGER.removeHandler(handler)

    payload = json.loads(handler.records[-1].message)
    assert payload["value"] == "api_key=[REDACTED:CREDENTIAL]"
    assert "private-object-value" not in handler.records[-1].message


def test_timed_node_logs_start_and_finish():
    handler = ListHandler()
    observability._LOGGER.addHandler(handler)

    try:
        with trace_context(trace_id="trace-test"):
            with timed_node("sample_node", phase="unit"):
                pass
    finally:
        observability._LOGGER.removeHandler(handler)

    events = [json.loads(record.message)["event"] for record in handler.records]
    assert "node.started" in events
    assert "node.finished" in events


def test_timed_node_logs_safe_error_fields_without_raw_exception_text():
    handler = ListHandler()
    observability._LOGGER.addHandler(handler)

    try:
        with pytest.raises(TimeoutError):
            with timed_node("sample_node", phase="unit"):
                raise TimeoutError("Bearer private-token and internal URL")
    finally:
        observability._LOGGER.removeHandler(handler)

    payload = next(
        json.loads(record.message)
        for record in handler.records
        if json.loads(record.message)["event"] == "node.error"
    )
    assert payload["error_code"] == "dependency_timeout"
    assert payload["retryable"] is True
    assert payload["cause_type"] == "TimeoutError"
    assert "private-token" not in json.dumps(payload)
