from datetime import UTC, datetime

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from tech_doc_agent.app.core.budget import BudgetUsage
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tech_doc_agent.app.core.retry import RetryExecutor, RetryPolicy
from tech_doc_agent.app.graph.budgeting import (
    WorkflowBudgetTracker,
    budgeted_request_start_node,
)
from tech_doc_agent.app.graph.nodes import assistant_node
from tech_doc_agent.app.graph.specs import ReflectionPolicy, ToolExecutionPolicy
from tech_doc_agent.app.graph.tool_nodes import create_tool_node_with_fallback
from tech_doc_agent.app.services.assistants.assistant_base import Assistant
from tests.test_model_pricing import PRICE_TABLE_PAYLOAD


class SequencedRunnable:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def invoke(self, state, config=None):
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output

    async def ainvoke(self, state, config=None):
        return self.invoke(state, config)


def _message(content: str, input_tokens: int, output_tokens: int) -> AIMessage:
    return AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={
            "model_provider": "openai_compatible",
            "model_name": "test-model",
        },
    )


def _tracker(events=None) -> WorkflowBudgetTracker:
    events = events if events is not None else []
    return WorkflowBudgetTracker(
        ModelPriceTable.from_payload(PRICE_TABLE_PAYLOAD),
        clock=lambda: datetime(2026, 7, 12, tzinfo=UTC),
        event_logger=lambda event, **fields: events.append({"event": event, **fields}),
    )


def test_request_start_replaces_prior_workflow_usage_with_new_timestamp():
    tracker = _tracker()
    old_usage = BudgetUsage.new(now=datetime(2026, 7, 11, tzinfo=UTC)).record_tools(4)
    node = budgeted_request_start_node(
        lambda state, config: {"user_info": "profile"},
        tracker,
    )

    update = node({"messages": [], "budget_usage": old_usage.to_state()}, None)

    usage = BudgetUsage.from_state(update["budget_usage"])
    assert update["user_info"] == "profile"
    assert usage.workflow_started_at == "2026-07-12T00:00:00+00:00"
    assert usage.llm_calls == 0
    assert usage.tool_calls == 0


def test_assistant_node_accounts_transport_retry_empty_response_and_final_response():
    events = []
    runnable = SequencedRunnable(
        [
            TimeoutError("private endpoint"),
            _message("", 10, 1),
            _message("final", 20, 2),
        ]
    )
    retry_executor = RetryExecutor(
        RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        sleeper=lambda delay: None,
        event_logger=lambda event, **fields: None,
    )
    assistant = Assistant(
        runnable,
        name="parser",
        max_empty_response_retries=1,
        retry_executor=retry_executor,
        default_provider="openai_compatible",
    )
    node = assistant_node(assistant, budget_tracker=_tracker(events))

    update = node.invoke({"messages": []})

    usage = BudgetUsage.from_state(update["budget_usage"])
    assert update["messages"].content == "final"
    assert "_llm_usage" not in update
    assert usage.llm_calls == 3
    assert usage.reported_input_tokens == 30
    assert usage.reported_output_tokens == 3
    assert usage.reported_total_tokens == 33
    assert usage.unreported_total_token_calls == 1
    assert usage.total_tokens is None
    assert usage.priced_cost_usd > 0
    assert usage.estimated_cost_usd is None
    assert update["budget_usage_delta"]["kind"] == "llm"
    assert update["budget_usage_delta"]["llm_calls"] == 3
    assert update["budget_usage_delta"]["reported_total_tokens"] == 33
    assert update["budget_usage_delta"]["total_tokens"] is None
    assert update["budget_usage_delta"]["estimated_cost_usd"] is None
    llm_events = [event for event in events if event["event"] == "budget.usage.llm"]
    assert len(llm_events) == 3
    assert llm_events[0]["pricing_reason"] == "token_usage_unreported"
    assert "private endpoint" not in str(events)


def test_tool_node_counts_success_and_failure_but_not_policy_block():
    calls = []

    @tool
    def recording_tool(query: str) -> str:
        """Record one real tool execution."""
        calls.append(query)
        if query == "fail":
            raise RuntimeError("private tool failure")
        return "ok"

    tracker = _tracker()
    execution_policy = ToolExecutionPolicy(
        max_identical_repeats=2,
        parser_max_retrieval_calls=6,
    )
    node = create_tool_node_with_fallback(
        [recording_tool],
        execution_policy,
        ReflectionPolicy(max_rounds=1),
        tracker,
    )
    initial_usage = BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC))

    success = node.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    name="parser",
                    tool_calls=[
                        {
                            "name": "recording_tool",
                            "args": {"query": "ok"},
                            "id": "call-success",
                        }
                    ],
                )
            ],
            "dialog_state": ["parser"],
            "budget_usage": initial_usage.to_state(),
        }
    )
    success_usage = BudgetUsage.from_state(success["budget_usage"])
    assert success_usage.tool_calls == 1
    assert success["budget_usage_delta"]["kind"] == "tool"
    assert success["budget_usage_delta"]["tool_calls"] == 1

    failed = node.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    name="parser",
                    tool_calls=[
                        {
                            "name": "recording_tool",
                            "args": {"query": "fail"},
                            "id": "call-fail",
                        }
                    ],
                )
            ],
            "dialog_state": ["parser"],
            "budget_usage": success_usage.to_state(),
        }
    )
    failed_usage = BudgetUsage.from_state(failed["budget_usage"])
    assert failed_usage.tool_calls == 2
    assert calls == ["ok", "fail"]
    assert "private tool failure" not in failed["messages"][0].content

    blocked_node = create_tool_node_with_fallback(
        [recording_tool],
        ToolExecutionPolicy(
            max_identical_repeats=0,
            parser_max_retrieval_calls=6,
        ),
        ReflectionPolicy(max_rounds=1),
        tracker,
    )
    prior_call = AIMessage(
        content="",
        name="parser",
        tool_calls=[
            {
                "name": "recording_tool",
                "args": {"query": "blocked"},
                "id": "call-prior",
            }
        ],
    )
    blocked = blocked_node.invoke(
        {
            "messages": [
                prior_call,
                ToolMessage(content="prior", tool_call_id="call-prior"),
                AIMessage(
                    content="",
                    name="parser",
                    tool_calls=[
                        {
                            "name": "recording_tool",
                            "args": {"query": "blocked"},
                            "id": "call-blocked",
                        }
                    ],
                ),
            ],
            "dialog_state": ["parser"],
            "budget_usage": failed_usage.to_state(),
        }
    )

    assert "budget_usage" not in blocked
    assert calls == ["ok", "fail"]
    assert blocked["messages"][0].artifact["error"]["code"] == "repeated_tool_call_blocked"
