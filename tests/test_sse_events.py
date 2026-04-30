import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from tech_doc_agent.app.api.routes.chat import (
    aiter_with_trace_context,
    astream_parts_as_sse,
    iter_update_events,
    iter_with_trace_context,
    router,
    sse_event,
    stream_parts_as_sse,
)
from tech_doc_agent.app.core.observability import get_trace_context


class FakeRuntime:
    def has_pending_interrupt(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        return False

    async def ahas_pending_interrupt(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        return False


class FakeRouteRuntime(FakeRuntime):
    async def aget_session_state(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict:
        return {
            "session_id": session_id,
            "user_id": user_id,
            "namespace": namespace,
            "exists": False,
            "pending_interrupt": False,
            "learning_target": None,
            "message_count": 0,
            "current_agent": "primary",
            "workflow_plan": [],
            "plan_index": 0,
        }

    async def astream_user_message(
        self,
        session_id: str,
        message: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        yield (
            "messages",
            (
                AIMessageChunk(content="hello"),
                {"langgraph_node": "primary"},
            ),
        )


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
                    "finish_parser": {
                        "parser_result": {
                            "topic": "LangGraph StateGraph",
                            "raw_text": "## 文档主题\nLangGraph StateGraph",
                            "parsed": True,
                        },
                        "plan_index": 1,
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
    assert "structured_result" in event_names

    structured_event = next(event for event in events if event.event == "structured_result")
    assert structured_event.data["result_key"] == "parser_result"
    assert structured_event.data["result"]["topic"] == "LangGraph StateGraph"
    assert structured_event.data["parsed"] is True


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
        user_id="user-a",
        namespace="tenant-docs",
    )

    first = next(wrapped)
    assert first.data["trace_id"] == "trace-test"
    assert first.data["session_id"] == "session-1"
    assert first.data["user_id"] == "user-a"
    assert first.data["namespace"] == "tenant-docs"
    assert get_trace_context() == {}

    second = next(wrapped)
    assert second.data["trace_id"] == "trace-test"
    assert second.data["session_id"] == "session-1"
    assert get_trace_context() == {}


def test_aiter_with_trace_context_sets_context_per_next_without_leaking():
    async def collect():
        async def source():
            assert get_trace_context()["trace_id"] == "trace-async"
            yield sse_event("first", {})
            assert get_trace_context()["trace_id"] == "trace-async"
            yield sse_event("second", {})

        wrapped = aiter_with_trace_context(
            source(),
            trace_id="trace-async",
            session_id="session-async",
            operation="chat",
            user_id="user-a",
            namespace="tenant-docs",
        )

        events = []
        async for event in wrapped:
            events.append(event)
            assert get_trace_context() == {}
        return events

    first, second = asyncio.run(collect())

    assert first.data["trace_id"] == "trace-async"
    assert first.data["session_id"] == "session-async"
    assert first.data["user_id"] == "user-a"
    assert first.data["namespace"] == "tenant-docs"
    assert second.data["trace_id"] == "trace-async"
    assert second.data["session_id"] == "session-async"


def test_astream_parts_as_sse_accepts_langgraph_tuple_messages():
    async def collect():
        async def parts():
            yield (
                "messages",
                (
                    AIMessageChunk(content="hello"),
                    {"langgraph_node": "primary"},
                ),
            )

        events = []
        async for event in astream_parts_as_sse(FakeRuntime(), "session-1", parts()):
            events.append(event)
        return events

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["token", "done"]


def test_chat_route_returns_async_sse_stream():
    app = FastAPI()
    app.state.runtime = FakeRouteRuntime()
    app.include_router(router)

    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": "session-async-route",
            "message": "hi",
            "trace_id": "trace-route",
            "user_id": "user-a",
            "namespace": "tenant-docs",
        },
    )

    assert response.status_code == 200
    assert "event: session_snapshot" in response.text
    assert "event: token" in response.text
    assert "event: done" in response.text
    assert "trace-route" in response.text
    assert "user-a" in response.text
    assert "tenant-docs" in response.text
