from langchain_core.messages import AIMessage, HumanMessage

from tech_doc_agent.app.core.budget import LlmUsage
from tech_doc_agent.app.core.context_metrics import ContextMetrics, ContextSnapshot
from tech_doc_agent.app.core.budget import BudgetUsage
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tech_doc_agent.app.graph.budgeting import WorkflowBudgetTracker
from tech_doc_agent.app.graph.context_metrics import (
    ContextMetricsTracker,
    context_metrics_request_start_node,
)
from tech_doc_agent.app.graph.nodes import assistant_node
from tech_doc_agent.app.agents.assistant_base import Assistant


class OneMessageRunnable:
    def __init__(self, message):
        self.message = message

    def invoke(self, state, config=None):
        return self.message

    async def ainvoke(self, state, config=None):
        return self.message


def _message(content: str, input_tokens: int = 20) -> AIMessage:
    return AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": 5,
            "total_tokens": input_tokens + 5,
        },
        response_metadata={
            "model_provider": "provider",
            "model_name": "model",
        },
    )


def test_request_start_replaces_prior_context_metrics_with_versioned_empty_state():
    tracker = ContextMetricsTracker(event_logger=lambda event, **fields: None)
    prior = ContextMetrics.new().record(
        ContextSnapshot("primary", "full", 3, 300, 3, 250),
        (LlmUsage(1, "provider", "model", 20, 5, 25),),
    )
    node = context_metrics_request_start_node(
        lambda state, config: {"user_info": "profile"},
        tracker,
    )

    update = node({"messages": [], "context_metrics": prior.to_state()}, None)

    assert update["user_info"] == "profile"
    assert update["context_metrics"] == ContextMetrics.new().to_state()
    assert update["context_metrics_delta"] == {"kind": "reset"}


def test_scoped_assistant_records_checkpoint_prompt_and_provider_token_gap():
    events = []
    tracker = ContextMetricsTracker(
        event_logger=lambda event, **fields: events.append(
            {"event": event, **fields}
        )
    )
    node = assistant_node(
        Assistant(OneMessageRunnable(_message("parsed")), name="parser"),
        scoped_messages=True,
        context_tracker=tracker,
    )
    state = {
        "messages": [
            HumanMessage(content="private user question"),
            AIMessage(content="private primary history", name="primary"),
        ],
        "user_info": "",
        "dialog_state": ["parser"],
        "learning_target": "RAG",
        "workflow_plan": ["parser"],
        "plan_index": 0,
    }

    update = node.invoke(state)

    metrics = ContextMetrics.from_state(update["context_metrics"])
    parser = metrics.agents["parser"]
    assert parser.scope == "scoped"
    assert parser.last_checkpoint_message_count == 2
    assert parser.last_prompt_message_count == 1
    assert parser.reported_input_tokens == 20
    assert update["context_metrics_delta"]["input_tokens"] == 20
    assert "_llm_usage" not in update
    assert [event["event"] for event in events] == [
        "context.input.measured",
        "context.input.completed",
    ]
    assert "private user question" not in str(events)
    assert "private primary history" not in str(events)


def test_full_primary_context_records_equal_prompt_and_checkpoint_message_counts():
    tracker = ContextMetricsTracker(event_logger=lambda event, **fields: None)
    node = assistant_node(
        Assistant(OneMessageRunnable(_message("answer", 30)), name="primary"),
        context_tracker=tracker,
    )
    state = {
        "messages": [
            HumanMessage(content="question"),
            AIMessage(content="history", name="primary"),
        ]
    }

    update = node.invoke(state)

    primary = ContextMetrics.from_state(update["context_metrics"]).agents["primary"]
    assert primary.scope == "full"
    assert primary.last_checkpoint_message_count == 2
    assert primary.last_prompt_message_count == 2
    assert primary.last_checkpoint_serialized_bytes is not None
    assert primary.last_prompt_serialized_bytes is not None
    assert primary.last_checkpoint_serialized_bytes >= primary.last_prompt_serialized_bytes


def test_context_metrics_accumulate_across_resume_without_start_reset():
    tracker = ContextMetricsTracker(event_logger=lambda event, **fields: None)
    initial = ContextMetrics.new().record(
        ContextSnapshot("summary", "full", 8, 800, 8, 750),
        (LlmUsage(1, "provider", "model", 100, 10, 110),),
    )
    snapshot = ContextSnapshot("summary", "full", 10, 1000, 10, 950)
    update = tracker.record_assistant(
        {"messages": [], "context_metrics": initial.to_state()},
        {
            "messages": _message("resumed", 120),
            "_llm_usage": (
                LlmUsage(1, "provider", "model", 120, 5, 125),
            ),
        },
        snapshot,
    )

    summary = ContextMetrics.from_state(update["context_metrics"]).agents["summary"]
    assert summary.invocations == 2
    assert summary.reported_input_tokens == 220
    assert summary.max_checkpoint_message_count == 10


def test_context_and_budget_trackers_consume_one_internal_usage_envelope_once():
    context_tracker = ContextMetricsTracker(event_logger=lambda event, **fields: None)
    budget_tracker = WorkflowBudgetTracker(
        ModelPriceTable.empty(),
        event_logger=lambda event, **fields: None,
    )
    node = assistant_node(
        Assistant(OneMessageRunnable(_message("answer", 40)), name="primary"),
        context_tracker=context_tracker,
        budget_tracker=budget_tracker,
    )

    update = node.invoke({"messages": [HumanMessage(content="question")]})

    assert ContextMetrics.from_state(update["context_metrics"]).agents[
        "primary"
    ].reported_input_tokens == 40
    assert BudgetUsage.from_state(update["budget_usage"]).llm_calls == 1
    assert "_llm_usage" not in update
