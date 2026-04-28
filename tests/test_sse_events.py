from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from tech_doc_agent.app.api.routes.chat import (
    iter_update_events,
    iter_with_trace_context,
    sse_event,
    stream_parts_as_sse,
)
from tech_doc_agent.app.core.observability import get_trace_context


class FakeRuntime:
    def has_pending_interrupt(self, session_id: str) -> bool:
        return False


def test_iter_update_events_emits_plan_transition_and_tool_events():
    events = list(
        iter_update_events(
            {
                "data": {
                    "store_plan": {
                        "workflow_plan": ["parser", "relation", "explanation"],
                        "plan_index": 0,
                        "learning_target": "LangGraph StateGraph",
                    },
                    "enter_parser": {},
                    "parser": {
                        "messages": [
                            AIMessage(
                                content="",
                                name="parser",
                                tool_calls=[
                                    {
                                        "name": "read_docs",
                                        "args": {"query": "LangGraph StateGraph"},
                                        "id": "call-1",
                                    }
                                ],
                            )
                        ]
                    },
                    "parser_assistant_safe_tools": {
                        "messages": [
                            ToolMessage(
                                content="[]",
                                name="read_docs",
                                tool_call_id="call-1",
                            )
                        ]
                    },
                }
            }
        )
    )

    event_names = [event.event for event in events]

    assert "plan_update" in event_names
    assert "agent_transition" in event_names
    assert "tool_call" in event_names
    assert "tool_result" in event_names


def test_iter_update_events_accepts_langgraph_tuple_updates():
    events = list(
        iter_update_events(
            (
                "updates",
                {
                    "store_plan": {
                        "workflow_plan": ["parser", "relation", "explanation"],
                        "plan_index": 0,
                        "learning_target": "LangGraph StateGraph",
                    }
                },
            )
        )
    )

    assert [event.event for event in events] == ["plan_update"]


def test_stream_parts_as_sse_accepts_langgraph_tuple_messages():
    events = list(
        stream_parts_as_sse(
            FakeRuntime(),
            "session-1",
            [
                (
                    "messages",
                    (
                        AIMessageChunk(content="hello"),
                        {"langgraph_node": "primary"},
                    ),
                )
            ],
        )
    )

    assert [event.event for event in events] == ["token", "done"]


def test_iter_with_trace_context_sets_context_per_next_without_leaking():
    def source():
        assert get_trace_context()["trace_id"] == "trace-test"
        yield sse_event("first", {})
        assert get_trace_context()["trace_id"] == "trace-test"
        yield sse_event("second", {})

    wrapped = iter_with_trace_context(
        source(),
        trace_id="trace-test",
        session_id="session-1",
        operation="chat",
    )

    first = next(wrapped)
    assert first.data["trace_id"] == "trace-test"
    assert first.data["session_id"] == "session-1"
    assert get_trace_context() == {}

    second = next(wrapped)
    assert second.data["trace_id"] == "trace-test"
    assert second.data["session_id"] == "session-1"
    assert get_trace_context() == {}
