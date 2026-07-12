from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from tech_doc_agent.app.core.context_compaction import (
    ContextCompactionPolicy,
    plan_context_compaction,
)
from tech_doc_agent.app.core.conversation_summary import (
    ConversationSummary,
    SummarySourceRange,
)
from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.graph import build_multi_agentic_graph
from tech_doc_agent.app.graph.context_compaction import ContextCompactor
from tech_doc_agent.app.services.conversation_summarizer import (
    ExtractiveConversationSummarizer,
)
from tech_doc_agent.app.graph.message_scope import build_assistant_state


def _closed_turn(index: int) -> list:
    return [
        HumanMessage(id=f"h-{index}", content=f"question {index}"),
        AIMessage(id=f"a-{index}", name="primary", content=f"answer {index}"),
    ]


def _summary(content: str = "Earlier discussion") -> ConversationSummary:
    return ConversationSummary.create(
        generator_id="test-generator-v1",
        content=content,
        source_range=SummarySourceRange(
            start_message_id="old-h",
            end_message_id="old-a",
            message_count=2,
            content_sha256="0" * 64,
        ),
    )


def test_conversation_summary_round_trips_and_detects_tampering():
    messages = _closed_turn(1)
    source = SummarySourceRange.from_messages(messages)
    summary = ConversationSummary.create(
        generator_id="extractive-v1",
        content="User asked a question and the assistant answered.",
        source_range=source,
    )

    assert ConversationSummary.from_state(summary.to_state()) == summary
    assert summary.covered_message_count == 2
    assert summary.source_ranges[0].start_message_id == "h-1"

    next_summary = ConversationSummary.create(
        generator_id="extractive-v2",
        content="Updated closed-turn summary.",
        source_range=SummarySourceRange.from_messages(_closed_turn(2)),
        previous=summary,
    )
    assert next_summary.predecessor_summary_id == summary.summary_id
    assert next_summary.covered_message_count == 4
    assert len(next_summary.source_ranges) == 2
    assert ConversationSummary.from_state(next_summary.to_state()) == next_summary

    tampered = summary.to_state()
    tampered["content"] = "changed after hashing"
    with pytest.raises(ValidationError) as exc_info:
        ConversationSummary.from_state(tampered)

    assert exc_info.value.code == "conversation_summary_invalid"


def test_extractive_summary_never_copies_raw_tool_payload_or_arguments():
    summarizer = ExtractiveConversationSummarizer(max_entry_chars=200)
    messages = [
        HumanMessage(id="h-1", content="Find the relevant docs"),
        AIMessage(
            id="a-1",
            name="parser",
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "read_docs",
                    "args": {"query": "private-query-value"},
                }
            ],
        ),
        ToolMessage(
            id="t-1",
            name="read_docs",
            tool_call_id="call-1",
            content="private raw document payload",
        ),
    ]

    content = summarizer.summarize(previous=None, messages=messages, max_chars=512)

    assert "Find the relevant docs" in content
    assert "requested tools: read_docs" in content
    assert "Tool[read_docs] success" in content
    assert "private-query-value" not in content
    assert "private raw document payload" not in content


def test_summary_is_bounded_while_preserving_oldest_and_newest_context():
    summarizer = ExtractiveConversationSummarizer(max_entry_chars=500)
    previous = _summary("oldest-context " + "a" * 300)

    content = summarizer.summarize(
        previous=previous,
        messages=[HumanMessage(id="h-new", content="newest-context " + "b" * 300)],
        max_chars=256,
    )

    assert len(content) == 256
    assert "oldest-context" in content
    assert "newest-context" in content
    assert "summary was compacted" in content


def test_planner_selects_only_closed_prefix_and_keeps_recent_turns():
    messages = [
        *_closed_turn(1),
        *_closed_turn(2),
        HumanMessage(id="h-3", content="current question"),
    ]

    decision = plan_context_compaction(
        {"messages": messages},
        ContextCompactionPolicy(max_messages=4, keep_recent_turns=2),
    )

    assert decision.should_compact is True
    assert decision.plan is not None
    assert [message.id for message in decision.plan.source_messages] == ["h-1", "a-1"]
    assert [message.id for message in decision.plan.retained_messages] == [
        "h-2",
        "a-2",
        "h-3",
    ]


def test_planner_is_disabled_by_default_and_supports_byte_only_threshold():
    state = {
        "messages": [*_closed_turn(1), HumanMessage(id="h-2", content="current")]
    }

    disabled = plan_context_compaction(state, ContextCompactionPolicy())
    byte_triggered = plan_context_compaction(
        state,
        ContextCompactionPolicy(
            max_messages=0,
            max_serialized_bytes=1,
            keep_recent_turns=1,
        ),
    )

    assert disabled.skip_reason == "disabled"
    assert byte_triggered.should_compact is True


@pytest.mark.parametrize(
    ("state_update", "reason"),
    [
        ({"dialog_state": ["parser"]}, "active_dialog"),
        ({"workflow_plan": ["parser"], "plan_index": 0}, "active_workflow"),
        ({"reflection_status": "repairing"}, "active_reflection"),
    ],
)
def test_planner_preserves_active_workflow_state(state_update, reason):
    state = {
        "messages": [*_closed_turn(1), HumanMessage(id="h-2", content="current")],
        **state_update,
    }

    decision = plan_context_compaction(
        state,
        ContextCompactionPolicy(max_messages=1, keep_recent_turns=1),
    )

    assert decision.should_compact is False
    assert decision.skip_reason == reason


def test_planner_refuses_to_split_pending_tool_call_from_result():
    messages = [
        HumanMessage(id="h-1", content="search"),
        AIMessage(
            id="a-1",
            name="primary",
            content="",
            tool_calls=[{"id": "call-1", "name": "read_docs", "args": {}}],
        ),
        HumanMessage(id="h-2", content="current"),
    ]

    decision = plan_context_compaction(
        {"messages": messages},
        ContextCompactionPolicy(max_messages=1, keep_recent_turns=1),
    )

    assert decision.should_compact is False
    assert decision.skip_reason == "open_tool_exchange"


def test_compactor_uses_remove_all_reducer_semantics_and_records_lineage():
    events = []
    compactor = ContextCompactor(
        policy=ContextCompactionPolicy(max_messages=4, keep_recent_turns=2),
        summarizer=ExtractiveConversationSummarizer(),
        event_logger=lambda event, **fields: events.append((event, fields)),
    )
    messages = [
        *_closed_turn(1),
        *_closed_turn(2),
        HumanMessage(id="h-3", content="current question"),
    ]

    update = compactor({"messages": messages})
    summary = ConversationSummary.from_state(update["conversation_summary"])
    reduced_messages = add_messages(messages, update["messages"])

    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    assert [message.id for message in reduced_messages] == ["h-2", "a-2", "h-3"]
    assert summary.covered_message_count == 2
    assert summary.source_ranges[0].end_message_id == "a-1"
    assert events[0][0] == "context.compacted"
    assert events[0][1]["removed_message_count"] == 2


def test_compactor_fails_open_when_legacy_source_messages_have_no_ids():
    events = []
    compactor = ContextCompactor(
        policy=ContextCompactionPolicy(max_messages=1, keep_recent_turns=1),
        summarizer=ExtractiveConversationSummarizer(),
        event_logger=lambda event, **fields: events.append((event, fields)),
    )
    state = {
        "messages": [
            HumanMessage(content="old question"),
            AIMessage(content="old answer"),
            HumanMessage(content="current question"),
        ]
    }

    assert compactor(state) == {}
    assert events == [
        (
            "context.compaction.skipped",
            {
                "reason": "source_metadata_unavailable",
                "checkpoint_message_count": 3,
            },
        )
    ]


def test_full_agents_receive_summary_but_scoped_agents_do_not_receive_it_or_foreign_tools():
    summary = _summary("summary-only-marker")
    state = {
        "messages": [
            HumanMessage(id="h-current", content="current request"),
            AIMessage(
                id="a-relation",
                name="relation",
                content="",
                tool_calls=[{"id": "call-foreign", "name": "read_docs", "args": {}}],
            ),
            ToolMessage(
                id="t-foreign",
                name="read_docs",
                tool_call_id="call-foreign",
                content="foreign-raw-tool-marker",
            ),
        ],
        "conversation_summary": summary.to_state(),
        "learning_target": "RAG",
        "workflow_plan": ["parser"],
        "plan_index": 0,
    }

    full_state = build_assistant_state(state, "primary", scoped_messages=False)
    scoped_state = build_assistant_state(state, "parser", scoped_messages=True)
    scoped_text = "\n".join(str(message.content) for message in scoped_state["messages"])

    assert full_state["messages"][0].name == "conversation_summary"
    assert "summary-only-marker" in full_state["messages"][0].content
    assert "summary-only-marker" not in scoped_text
    assert "foreign-raw-tool-marker" not in scoped_text


def test_compiled_graph_compacts_only_at_next_request_start(graph_spec):
    events = []
    compactor = ContextCompactor(
        policy=ContextCompactionPolicy(max_messages=3, keep_recent_turns=1),
        summarizer=ExtractiveConversationSummarizer(),
        event_logger=lambda event, **fields: events.append((event, fields)),
    )
    graph = build_multi_agentic_graph(
        MemorySaver(),
        replace(graph_spec, context_compactor=compactor),
    )
    config = {
        "configurable": {"thread_id": "user-a:docs-a:context-compaction"},
        "metadata": {"user_id": "user-a", "namespace": "docs-a"},
    }

    graph.invoke({"messages": [("user", "question 1")]}, config)
    graph.invoke({"messages": [("user", "question 2")]}, config)
    graph.invoke({"messages": [("user", "question 3")]}, config)
    state = graph.get_state(config).values
    summary = ConversationSummary.from_state(state["conversation_summary"])

    assert [message.content for message in state["messages"]] == ["question 3", "stub"]
    assert summary.covered_message_count == 4
    assert [event for event, _ in events] == ["context.compacted"]
