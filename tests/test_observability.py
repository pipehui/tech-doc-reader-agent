import json
import logging

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
