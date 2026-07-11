from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from tech_doc_agent.app.graph.tool_nodes import create_tool_node_with_fallback, handle_tool_error


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
                    {"id": "call-1"},
                    {"id": "call-2"},
                ]
            )
        ],
    }

    result = handle_tool_error(state)

    assert [message["tool_call_id"] for message in result["messages"]] == [
        "call-1",
        "call-2",
    ]
    assert {message["status"] for message in result["messages"]} == {"error"}
    assert all("RuntimeError" in message["content"] for message in result["messages"])


def test_tool_node_fallback_preserves_error_status_after_message_conversion():
    node = create_tool_node_with_fallback([exploding_tool])
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
    assert "RuntimeError" in message.content
