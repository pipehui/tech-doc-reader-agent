import json
import logging

import pytest
from langchain_core.messages import AIMessage

from tech_doc_agent.app.core import observability
from tech_doc_agent.app.services.assistants.assistant_base import (
    Assistant,
    is_empty_assistant_output,
)


class FakeRunnable:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.states = []

    def invoke(self, state, config=None):
        self.states.append(state)
        return self.outputs.pop(0)


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture_observability_logs():
    handler = ListHandler()
    observability._LOGGER.addHandler(handler)
    return handler


def _log_events(handler):
    return [json.loads(record.message) for record in handler.records]


def test_assistant_retries_empty_response_and_returns_next_result():
    runnable = FakeRunnable(
        [
            AIMessage(content=""),
            AIMessage(content="real answer"),
        ]
    )
    assistant = Assistant(runnable, name="tester", max_retries=2)
    handler = _capture_observability_logs()

    try:
        result = assistant({"messages": [("user", "hi")]})
    finally:
        observability._LOGGER.removeHandler(handler)

    assert result["messages"].content == "real answer"
    assert result["messages"].name == "tester"
    assert len(runnable.states) == 2
    assert runnable.states[1]["messages"][-1] == ("user", "Respond with a real output.")

    events = _log_events(handler)
    assert [event["event"] for event in events] == ["assistant.empty_response"]
    assert events[0]["assistant"] == "tester"
    assert events[0]["attempt"] == 1


def test_assistant_raises_after_empty_response_retry_budget_is_exhausted():
    runnable = FakeRunnable(
        [
            AIMessage(content=""),
            AIMessage(content=[]),
        ]
    )
    assistant = Assistant(runnable, name="tester", max_retries=1)
    handler = _capture_observability_logs()

    try:
        with pytest.raises(RuntimeError, match="returned empty output"):
            assistant({"messages": [("user", "hi")]})
    finally:
        observability._LOGGER.removeHandler(handler)

    assert len(runnable.states) == 2

    events = _log_events(handler)
    assert [event["event"] for event in events] == [
        "assistant.empty_response",
        "assistant.empty_response",
        "assistant.empty_response.exhausted",
    ]
    assert events[-1]["assistant"] == "tester"


def test_assistant_does_not_retry_empty_tool_call_response():
    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_docs",
                "args": {"query": "StateGraph"},
                "id": "call-1",
            }
        ],
    )
    runnable = FakeRunnable([tool_call_message])
    assistant = Assistant(runnable, name="tester", max_retries=2)

    result = assistant({"messages": [("user", "hi")]})

    assert result["messages"].tool_calls[0]["name"] == "read_docs"
    assert len(runnable.states) == 1
    assert is_empty_assistant_output(tool_call_message) is False


def test_assistant_rejects_negative_max_retries():
    with pytest.raises(ValueError, match="max_retries"):
        Assistant(FakeRunnable([]), max_retries=-1)
