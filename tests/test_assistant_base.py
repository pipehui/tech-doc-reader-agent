import asyncio
import json
import logging

import pytest
from langchain_core.messages import AIMessage

from tech_doc_agent.app.core.errors import Timeout
from tech_doc_agent.app.core import observability
from tech_doc_agent.app.core.retry import RetryExecutor, RetryPolicy
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

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return self.outputs.pop(0)


class FailingRunnable:
    def invoke(self, state, config=None):
        raise TimeoutError("provider URL and bearer token are private")

    async def ainvoke(self, state, config=None):
        raise TimeoutError("provider URL and bearer token are private")


class SequencedRunnable:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.states = []

    def invoke(self, state, config=None):
        self.states.append(state)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output


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
    assistant = Assistant(runnable, name="tester", max_empty_response_retries=2)
    handler = _capture_observability_logs()

    try:
        result = assistant({"messages": [("user", "hi")]})
    finally:
        observability._LOGGER.removeHandler(handler)

    assert result["messages"].content == "real answer"
    assert result["messages"].name == "tester"
    assert len(runnable.states) == 2
    assert runnable.states[1]["messages"][-1].content == "Respond with a real output."

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
    assistant = Assistant(runnable, name="tester", max_empty_response_retries=1)
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
    assistant = Assistant(runnable, name="tester", max_empty_response_retries=2)

    result = assistant({"messages": [("user", "hi")]})

    assert result["messages"].tool_calls[0]["name"] == "read_docs"
    assert len(runnable.states) == 1
    assert is_empty_assistant_output(tool_call_message) is False


def test_assistant_rejects_negative_empty_response_retries():
    with pytest.raises(ValueError, match="max_empty_response_retries"):
        Assistant(FakeRunnable([]), max_empty_response_retries=-1)


def test_assistant_maps_llm_transport_failure_without_exposing_provider_text():
    assistant = Assistant(FailingRunnable(), name="tester")

    with pytest.raises(Timeout) as exc_info:
        assistant({"messages": [("user", "hi")]})

    assert exc_info.value.dependency == "llm"
    assert exc_info.value.cause_type == "TimeoutError"
    assert "bearer token" not in str(exc_info.value)


def test_assistant_keeps_transport_retry_separate_from_empty_response_repair():
    runnable = SequencedRunnable(
        [
            TimeoutError("private endpoint"),
            AIMessage(content=""),
            AIMessage(content="real answer"),
        ]
    )
    retry_events = []
    retry_executor = RetryExecutor(
        RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        sleeper=lambda delay: None,
        event_logger=lambda event, **fields: retry_events.append((event, fields)),
    )
    assistant = Assistant(
        runnable,
        name="tester",
        max_empty_response_retries=1,
        retry_executor=retry_executor,
    )

    result = assistant({"messages": []})

    assert result["messages"].content == "real answer"
    assert len(runnable.states) == 3
    assert runnable.states[0]["messages"] == []
    assert runnable.states[1]["messages"] == []
    assert runnable.states[2]["messages"][-1].content == "Respond with a real output."
    final_events = [fields for event, fields in retry_events if event == "retry.final"]
    assert [event["attempts"] for event in final_events] == [2, 1]


def test_assistant_ainvoke_retries_empty_response_and_returns_next_result():
    async def run():
        runnable = FakeRunnable(
            [
                AIMessage(content=""),
                AIMessage(content="real async answer"),
            ]
        )
        assistant = Assistant(runnable, name="tester", max_empty_response_retries=2)
        return runnable, await assistant.ainvoke({"messages": [("user", "hi")]})

    runnable, result = asyncio.run(run())

    assert result["messages"].content == "real async answer"
    assert result["messages"].name == "tester"
    assert len(runnable.states) == 2
    assert runnable.states[1]["messages"][-1].content == "Respond with a real output."


def test_assistant_ainvoke_uses_transport_retry_executor():
    async def run():
        runnable = SequencedRunnable(
            [
                TimeoutError("private endpoint"),
                AIMessage(content="real async answer"),
            ]
        )

        async def no_sleep(delay):
            return None

        retry_executor = RetryExecutor(
            RetryPolicy(
                max_attempts=2,
                initial_delay_seconds=0,
                max_delay_seconds=0,
                jitter_ratio=0,
            ),
            async_sleeper=no_sleep,
            event_logger=lambda event, **fields: None,
        )
        assistant = Assistant(runnable, name="tester", retry_executor=retry_executor)
        result = await assistant.ainvoke({"messages": []})
        return runnable, result

    runnable, result = asyncio.run(run())

    assert result["messages"].content == "real async answer"
    assert len(runnable.states) == 2
