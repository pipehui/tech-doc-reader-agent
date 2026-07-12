import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

import tech_doc_agent.app.graph.tool_nodes as tool_nodes
from tech_doc_agent.app.graph.specs import ToolExecutionPolicy
from tech_doc_agent.app.graph.tool_nodes import create_tool_node_with_fallback, handle_tool_error


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
    blocked_event = next(event for event in events if event["event"] == "tool_call.blocked")
    assert blocked_event["policy_action"] == "block"
    assert blocked_event["reason"] == "repeated_tool_call"
    assert blocked_event["observed_calls"] == 3
    assert blocked_event["configured_limit"] == 2
