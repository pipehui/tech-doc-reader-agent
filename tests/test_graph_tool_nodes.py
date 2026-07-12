import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

import tech_doc_agent.app.graph.tool_nodes as tool_nodes
from tech_doc_agent.app.graph.specs import ToolExecutionPolicy
from tech_doc_agent.app.graph.provider_retries import ProviderRetryUsageTracker
from tech_doc_agent.app.graph.tool_nodes import create_tool_node_with_fallback, handle_tool_error
from tech_doc_agent.app.core.retry import build_retry_executor
from tech_doc_agent.app.core.settings import Settings


DEFAULT_TOOL_EXECUTION_POLICY = ToolExecutionPolicy(
    max_identical_repeats=2,
    parser_max_retrieval_calls=6,
)


@tool
def exploding_tool(query: str) -> str:
    """Raise a deterministic failure for the tool fallback contract test."""
    raise RuntimeError(f"offline: {query}")


def test_tool_fallback_marks_every_result_as_an_explicit_error():
    state = {
        "error": RuntimeError("offline"),
        "messages": [
            SimpleNamespace(
                tool_calls=[
                    {"id": "call-1", "name": "read_docs"},
                    {"id": "call-2", "name": "web_search"},
                ]
            )
        ],
    }

    result = handle_tool_error(state)

    assert [message.tool_call_id for message in result["messages"]] == [
        "call-1",
        "call-2",
    ]
    assert {message.status for message in result["messages"]} == {"error"}
    assert [message.artifact["error"]["dependency"] for message in result["messages"]] == [
        "document_repository",
        "web_search",
    ]
    assert all(message.artifact["error"]["code"] == "unknown_dependency_error" for message in result["messages"])
    assert all(message.artifact["error"]["cause_type"] == "RuntimeError" for message in result["messages"])
    assert all("offline" not in message.content for message in result["messages"])
    assert all(json.loads(message.content)["status"] == "error" for message in result["messages"])


def test_tool_node_fallback_preserves_error_status_after_message_conversion():
    node = create_tool_node_with_fallback([exploding_tool], DEFAULT_TOOL_EXECUTION_POLICY)
    result = node.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    name="parser",
                    tool_calls=[
                        {
                            "name": "exploding_tool",
                            "args": {"query": "StateGraph"},
                            "id": "call-error",
                        }
                    ],
                )
            ],
            "dialog_state": ["parser"],
        }
    )

    message = result["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-error"
    assert message.status == "error"
    assert message.name == "exploding_tool"
    assert message.artifact["error"]["code"] == "unknown_dependency_error"
    assert message.artifact["error"]["tool"] == "exploding_tool"
    assert message.artifact["error"]["cause_type"] == "RuntimeError"
    assert "offline: StateGraph" not in message.content
    assert result["reflection_status"] == "finalizing"
    assert result["reflection_rounds_used"] == 0
    assert result["reflection_terminal_reason"] == "non_repairable_error"


def test_async_tool_node_uses_the_same_structured_fallback_contract():
    node = create_tool_node_with_fallback([exploding_tool], DEFAULT_TOOL_EXECUTION_POLICY)

    result = asyncio.run(
        node.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        name="parser",
                        tool_calls=[
                            {
                                "name": "exploding_tool",
                                "args": {"query": "StateGraph"},
                                "id": "call-async-error",
                            }
                        ],
                    )
                ],
                "dialog_state": ["parser"],
            }
        )
    )

    message = result["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.artifact["error"]["code"] == "unknown_dependency_error"
    assert message.artifact["error"]["cause_type"] == "RuntimeError"
    assert "offline: StateGraph" not in message.content
    assert result["reflection_status"] == "finalizing"


def test_tool_node_records_provider_retry_usage_in_workflow_state():
    calls = 0
    executor = build_retry_executor(_retry_settings(max_attempts=2))

    @tool
    def retrying_provider_tool(query: str) -> str:
        """Call a fake provider that succeeds after one transport retry."""

        def request():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("private provider endpoint")
            return f"result:{query}"

        return executor.run(
            request,
            operation_name="embedding.create",
            dependency="embedding",
            tool="read_docs",
            idempotent=True,
        )

    tracker = ProviderRetryUsageTracker(event_logger=lambda event, **fields: None)
    node = create_tool_node_with_fallback(
        [retrying_provider_tool],
        DEFAULT_TOOL_EXECUTION_POLICY,
        provider_retry_tracker=tracker,
    )
    state = _tool_call_state("retrying_provider_tool", "call-retry")

    result = node.invoke(state)

    assert result["messages"][0].status == "success"
    assert result["provider_retry_usage"]["summary"] == {
        "operations": 1,
        "attempts": 2,
        "retries": 1,
        "waited_seconds": 0.0,
        "recovered_operations": 1,
        "exhausted_operations": 0,
        "failed_operations": 0,
        "dependencies": {
            "embedding": {
                "operations": 1,
                "attempts": 2,
                "retries": 1,
                "waited_seconds": 0.0,
            }
        },
    }
    assert result["provider_retry_usage_delta"]["kind"] == "operations"
    assert result["provider_retry_usage_delta"]["operations"][0]["operation"] == (
        "embedding.create"
    )


def test_async_tool_fallback_preserves_exhausted_provider_usage():
    executor = build_retry_executor(_retry_settings(max_attempts=1))

    @tool
    def exhausted_provider_tool(query: str) -> str:
        """Call a fake provider that exhausts its transport policy."""

        return executor.run(
            lambda: (_ for _ in ()).throw(TimeoutError(f"private:{query}")),
            operation_name="web_search.duckduckgo",
            dependency="duckduckgo",
            tool="web_search",
            idempotent=True,
        )

    tracker = ProviderRetryUsageTracker(event_logger=lambda event, **fields: None)
    node = create_tool_node_with_fallback(
        [exhausted_provider_tool],
        DEFAULT_TOOL_EXECUTION_POLICY,
        provider_retry_tracker=tracker,
    )

    result = asyncio.run(
        node.ainvoke(_tool_call_state("exhausted_provider_tool", "call-exhausted"))
    )

    assert result["messages"][0].status == "error"
    assert result["provider_retry_usage"]["summary"]["attempts"] == 1
    assert result["provider_retry_usage"]["summary"]["exhausted_operations"] == 1
    operation = result["provider_retry_usage_delta"]["operations"][0]
    assert operation["outcome"] == "exhausted"
    assert operation["error_code"] == "dependency_timeout"
    assert "private" not in str(operation)


def test_tool_node_logs_explicit_block_decision_with_configured_limit(monkeypatch):
    events = []
    monkeypatch.setattr(
        tool_nodes,
        "log_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )
    node = create_tool_node_with_fallback(
        [exploding_tool],
        ToolExecutionPolicy(
            max_identical_repeats=2,
            parser_max_retrieval_calls=6,
        ),
    )

    result = node.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    name="parser",
                    tool_calls=[
                        {
                            "name": "exploding_tool",
                            "args": {"query": "StateGraph"},
                            "id": "call-1",
                        }
                    ],
                ),
                ToolMessage(content="result", tool_call_id="call-1"),
                AIMessage(
                    content="",
                    name="parser",
                    tool_calls=[
                        {
                            "name": "exploding_tool",
                            "args": {"query": "StateGraph"},
                            "id": "call-2",
                        }
                    ],
                ),
                ToolMessage(content="result", tool_call_id="call-2"),
                AIMessage(
                    content="",
                    name="parser",
                    tool_calls=[
                        {
                            "name": "exploding_tool",
                            "args": {"query": "StateGraph"},
                            "id": "call-3",
                        }
                    ],
                ),
            ],
            "dialog_state": ["parser"],
        }
    )

    assert result["messages"][0].artifact["error"]["code"] == "repeated_tool_call_blocked"
    assert result["reflection_status"] == "finalizing"
    assert result["reflection_terminal_reason"] == "non_repairable_error"
    blocked_event = next(event for event in events if event["event"] == "tool_call.blocked")
    assert blocked_event["policy_action"] == "block"
    assert blocked_event["reason"] == "repeated_tool_call"
    assert blocked_event["observed_calls"] == 3
    assert blocked_event["configured_limit"] == 2


def _retry_settings(*, max_attempts: int) -> Settings:
    return Settings(
        TRANSPORT_RETRY_MAX_ATTEMPTS=max_attempts,
        TRANSPORT_RETRY_INITIAL_DELAY_SECONDS=0,
        TRANSPORT_RETRY_MAX_DELAY_SECONDS=0,
        TRANSPORT_RETRY_JITTER_RATIO=0,
    )


def _tool_call_state(tool_name: str, call_id: str) -> dict:
    return {
        "messages": [
            AIMessage(
                content="",
                name="parser",
                tool_calls=[
                    {
                        "name": tool_name,
                        "args": {"query": "StateGraph"},
                        "id": call_id,
                    }
                ],
            )
        ],
        "dialog_state": ["parser"],
    }
