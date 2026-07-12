import asyncio
from types import SimpleNamespace

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.conversation_summary import (
    ConversationSummary,
    SummarySourceRange,
)
from tech_doc_agent.app.services.chat_runtime import ChatRuntime


class FakeStateGraph:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.configs = []

    def get_state(self, config):
        self.configs.append(config)
        return self.snapshot


def _message(message_type: str, content, **attributes):
    defaults = {
        "id": None,
        "name": None,
        "tool_call_id": None,
        "tool_calls": [],
    }
    return SimpleNamespace(
        type=message_type,
        content=content,
        **{**defaults, **attributes},
    )


def _runtime_with_snapshot(snapshot) -> ChatRuntime:
    runtime = ChatRuntime()
    runtime.settings = Settings()
    runtime.graph = FakeStateGraph(snapshot)
    return runtime


def test_history_serializes_checkpoint_messages_without_losing_graph_details():
    tool_calls = [{"name": "search", "args": {"query": "RAG"}, "id": "call-1"}]
    snapshot = SimpleNamespace(
        next=("parser_assistant",),
        values={
            "user_id": "stored-user",
            "namespace": "stored-docs",
            "learning_target": "RAG",
            "messages": [
                _message("human", ["hello ", {"type": "text", "text": "world"}], id="human-1"),
                _message("ai", "searching", id="ai-1", name="parser", tool_calls=tool_calls),
                _message("tool", [{"text": "found"}], id="tool-1", name="search", tool_call_id="call-1"),
                _message("system", "internal", id="system-1"),
            ],
        },
    )
    runtime = _runtime_with_snapshot(snapshot)

    history = runtime.get_history("session-1", user_id="request-user", namespace="request-docs")

    assert history == {
        "session_id": "session-1",
        "user_id": "stored-user",
        "namespace": "stored-docs",
        "learning_target": "RAG",
        "pending_interrupt": True,
        "message_count": 4,
        "messages": [
            {
                "id": "human-1",
                "role": "user",
                "raw_type": "human",
                "content": "hello world",
                "name": None,
                "tool_call_id": None,
                "tool_calls": [],
            },
            {
                "id": "ai-1",
                "role": "assistant",
                "raw_type": "ai",
                "content": "searching",
                "name": "parser",
                "tool_call_id": None,
                "tool_calls": tool_calls,
            },
            {
                "id": "tool-1",
                "role": "tool",
                "raw_type": "tool",
                "content": "found",
                "name": "search",
                "tool_call_id": "call-1",
                "tool_calls": [],
            },
            {
                "id": "system-1",
                "role": "system",
                "raw_type": "system",
                "content": "internal",
                "name": None,
                "tool_call_id": None,
                "tool_calls": [],
            },
        ],
    }
    assert runtime.graph.configs[0]["configurable"]["thread_id"] == "request-user:request-docs:session-1"


def test_history_view_filters_internal_messages_and_optionally_includes_tools():
    snapshot = SimpleNamespace(
        next=(),
        values={
            "messages": [
                _message("human", "question", id="human-1"),
                _message("ai", "", id="ai-tool-call"),
                _message("tool", "tool result", id="tool-1", name="search", tool_call_id="call-1"),
                _message("ai", "answer", id="ai-1", name="primary"),
                _message("system", "internal", id="system-1"),
            ]
        },
    )
    runtime = _runtime_with_snapshot(snapshot)

    without_tools = runtime.get_history_view("session-1")
    with_tools = runtime.get_history_view("session-1", include_tools=True)

    assert without_tools["messages"] == [
        {"id": "human-1", "role": "user", "kind": "message", "content": "question"},
        {
            "id": "ai-1",
            "role": "assistant",
            "kind": "message",
            "content": "answer",
            "name": "primary",
        },
    ]
    assert without_tools["message_count"] == 2
    assert with_tools["messages"][1] == {
        "id": "tool-1",
        "role": "tool",
        "kind": "tool_result",
        "content": "tool result",
        "tool_call_id": "call-1",
        "name": "search",
    }
    assert with_tools["message_count"] == 3


def test_sync_and_async_session_state_views_are_equivalent():
    snapshot = SimpleNamespace(
        next=(),
        values={
            "messages": [_message("human", "question")],
            "learning_target": "RAG",
            "dialog_state": ["primary", "relation"],
            "workflow_plan": ["parse", "relate"],
            "plan_index": 1,
            "budget_usage": {
                "schema_version": 1,
                "llm_calls": 2,
                "tool_calls": 1,
            },
            "budget_status": "terminated",
            "budget_termination": {
                "schema_version": 1,
                "dimension": "llm_calls",
            },
            "context_metrics": {
                "schema_version": 1,
                "measurements": 2,
                "agents": {"primary": {"invocations": 2}},
            },
        },
    )
    runtime = _runtime_with_snapshot(snapshot)

    sync_state = runtime.get_session_state("session-1", user_id="user-a", namespace="docs-a")
    async_state = asyncio.run(
        runtime.aget_session_state("session-1", user_id="user-a", namespace="docs-a")
    )

    assert async_state == sync_state
    assert sync_state == {
        "session_id": "session-1",
        "user_id": "user-a",
        "namespace": "docs-a",
        "exists": True,
        "pending_interrupt": False,
        "learning_target": "RAG",
        "message_count": 1,
        "current_agent": "relation",
        "workflow_plan": ["parse", "relate"],
        "plan_index": 1,
        "budget_usage": {
            "schema_version": 1,
            "llm_calls": 2,
            "tool_calls": 1,
        },
        "budget_status": "terminated",
        "budget_termination": {
            "schema_version": 1,
            "dimension": "llm_calls",
        },
        "context_metrics": {
            "schema_version": 1,
            "measurements": 2,
            "agents": {"primary": {"invocations": 2}},
        },
    }


def test_compacted_history_projects_independent_summary_before_retained_messages():
    summary = ConversationSummary.create(
        generator_id="extractive-test-v1",
        content="Earlier user and assistant discussion.",
        source_range=SummarySourceRange(
            start_message_id="old-h",
            end_message_id="old-a",
            message_count=2,
            content_sha256="1" * 64,
        ),
    )
    snapshot = SimpleNamespace(
        next=(),
        values={
            "conversation_summary": summary.to_state(),
            "messages": [_message("human", "current question", id="current-h")],
        },
    )
    runtime = _runtime_with_snapshot(snapshot)

    history = runtime.get_history("session-1")
    history_view = runtime.get_history_view("session-1")
    session_state = runtime.get_session_state("session-1")

    assert history["message_count"] == 2
    assert history["messages"][0] == {
        "id": f"conversation-summary-{summary.summary_id}",
        "role": "system",
        "raw_type": "conversation_summary",
        "content": "Earlier user and assistant discussion.",
        "name": "conversation_summary",
        "tool_call_id": None,
        "tool_calls": [],
    }
    assert history_view["messages"][0]["kind"] == "conversation_summary"
    assert history_view["messages"][1]["content"] == "current question"
    assert session_state["message_count"] == 2
