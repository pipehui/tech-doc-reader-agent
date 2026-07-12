from dataclasses import replace
from datetime import UTC, datetime

import pytest

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from tech_doc_agent.app.core.budget import BudgetUsage, LlmUsage
from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.execution_budget import (
    REQUEST_BUDGET_METADATA_KEY,
    ExecutionBudget,
    RequestBudgetWindow,
)
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tech_doc_agent.app.core.retry import RetryExecutor, RetryPolicy
from tech_doc_agent.app.graph.budgeting import (
    WorkflowBudgetTracker,
    budgeted_request_start_node,
)
from tech_doc_agent.app.graph.budget_termination import create_budget_termination_node
from tech_doc_agent.app.graph.nodes import assistant_node
from tech_doc_agent.app.graph.reflection import route_after_tool_result
from tech_doc_agent.app.graph.routing import make_primary_router
from tech_doc_agent.app.graph.specs import ReflectionPolicy, ToolExecutionPolicy
from tech_doc_agent.app.graph.tool_nodes import create_tool_node_with_fallback
from tech_doc_agent.app.graph.builder import build_multi_agentic_graph
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
        wall_clock=lambda: datetime(2026, 7, 12, tzinfo=UTC),
        event_logger=lambda event, **fields: events.append({"event": event, **fields}),
    )


def _limited_tracker(
    budget: ExecutionBudget,
    *,
    events=None,
    monotonic_clock=lambda: 0.0,
) -> WorkflowBudgetTracker:
    events = events if events is not None else []
    return WorkflowBudgetTracker(
        ModelPriceTable.from_payload(PRICE_TABLE_PAYLOAD),
        execution_budget=budget,
        wall_clock=lambda: datetime(2026, 7, 12, tzinfo=UTC),
        monotonic_clock=monotonic_clock,
        event_logger=lambda event, **fields: events.append(
            {"event": event, **fields}
        ),
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


def test_assistant_precheck_stops_before_first_provider_call_at_call_cap():
    runnable = SequencedRunnable([_message("must not run", 1, 1)])
    tracker = _limited_tracker(ExecutionBudget(workflow_max_llm_calls=1))
    assistant = Assistant(runnable, name="primary")
    node = assistant_node(assistant, budget_tracker=tracker)
    prior = BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC))
    prior, _ = prior.record_llm(
        LlmUsage(1, "openai_compatible", "test-model", 1, 1, 2),
        ModelPriceTable.from_payload(PRICE_TABLE_PAYLOAD),
    )

    update = node.invoke({"messages": [], "budget_usage": prior.to_state()})

    assert len(runnable.outputs) == 1
    assert update["budget_status"] == "terminating"
    assert update["budget_termination"]["dimension"] == "llm_calls"
    assert update["budget_termination"]["observed"] == 2
    assert update["budget_usage_delta"] == {}
    assert make_primary_router(frozenset())(
        {"messages": [], **update}
    ) == "budget_terminated"


def test_retry_attempt_guard_accounts_failures_and_stops_before_overshoot():
    runnable = SequencedRunnable(
        [
            TimeoutError("private-1"),
            TimeoutError("private-2"),
            _message("must not run", 1, 1),
        ]
    )
    retry_executor = RetryExecutor(
        RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        sleeper=lambda delay: None,
        event_logger=lambda event, **fields: None,
    )
    tracker = _limited_tracker(ExecutionBudget(workflow_max_llm_calls=2))
    node = assistant_node(
        Assistant(
            runnable,
            name="primary",
            retry_executor=retry_executor,
        ),
        budget_tracker=tracker,
    )

    update = node.invoke({"messages": []})

    assert len(runnable.outputs) == 1
    usage = BudgetUsage.from_state(update["budget_usage"])
    assert usage.llm_calls == 2
    assert usage.unreported_total_token_calls == 2
    assert update["budget_status"] == "terminating"
    assert update["budget_usage_delta"]["llm_calls"] == 2
    assert update["budget_termination"]["observed"] == 3


def test_llm_token_overshoot_closes_unexecuted_tool_call_then_terminates():
    message = AIMessage(
        content="",
        name="primary",
        tool_calls=[
            {
                "name": "recording_tool",
                "args": {"query": "private"},
                "id": "call-over-budget",
            }
        ],
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        },
        response_metadata={
            "model_provider": "openai_compatible",
            "model_name": "test-model",
        },
    )
    tracker = _limited_tracker(ExecutionBudget(workflow_max_total_tokens=10))
    node = assistant_node(
        Assistant(SequencedRunnable([message]), name="primary"),
        budget_tracker=tracker,
    )

    update = node.invoke({"messages": []})

    assert update["budget_status"] == "terminating"
    assert update["budget_termination"]["dimension"] == "total_tokens"
    assert update["messages"][0].tool_calls[0]["id"] == "call-over-budget"
    assert isinstance(update["messages"][1], ToolMessage)
    assert update["messages"][1].tool_call_id == "call-over-budget"
    assert update["messages"][1].status == "error"
    assert (
        update["messages"][1].artifact["error"]["code"]
        == "execution_budget_exceeded"
    )

    terminal_state = {
        "messages": update["messages"],
        "dialog_state": [],
        **update,
    }
    terminal = create_budget_termination_node(tracker)(terminal_state)
    assert terminal["budget_status"] == "terminated"
    assert terminal["messages"][0].name == "primary"
    assert "安全停止" in terminal["messages"][0].content


def test_approval_resume_rechecks_tool_cap_before_sensitive_execution():
    calls = []

    @tool
    def sensitive_tool(value: str) -> str:
        """A fake sensitive write that must stay uncalled."""
        calls.append(value)
        return "written"

    tracker = _limited_tracker(ExecutionBudget(workflow_max_tool_calls=1))
    node = create_tool_node_with_fallback(
        [sensitive_tool],
        ToolExecutionPolicy(
            max_identical_repeats=2,
            parser_max_retrieval_calls=6,
        ),
        ReflectionPolicy(max_rounds=1),
        tracker,
    )
    usage = BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC)).record_tools(1)
    state = {
        "messages": [
            AIMessage(
                content="",
                name="primary",
                tool_calls=[
                    {
                        "name": "sensitive_tool",
                        "args": {"value": "private"},
                        "id": "call-resume",
                    }
                ],
            )
        ],
        "budget_usage": usage.to_state(),
    }

    update = node.invoke(
        state,
        {"metadata": {"runtime_operation": "approval"}},
    )

    assert calls == []
    assert update["budget_status"] == "terminating"
    assert update["budget_termination"]["phase"] == "resume"
    assert update["budget_termination"]["observed"] == 2
    assert BudgetUsage.from_state(update["budget_usage"]).tool_calls == 1
    assert route_after_tool_result({**state, **update}) == "budget_terminate"


def test_request_deadline_is_checked_again_after_tool_atomic_step():
    calls = []

    @tool
    def slow_tool(value: str) -> str:
        """A fake tool that advances the injected clock."""
        calls.append(value)
        return "done"

    times = iter([1.0, 3.0])
    tracker = _limited_tracker(
        ExecutionBudget(request_max_seconds=2),
        monotonic_clock=lambda: next(times),
    )
    node = create_tool_node_with_fallback(
        [slow_tool],
        ToolExecutionPolicy(
            max_identical_repeats=2,
            parser_max_retrieval_calls=6,
        ),
        ReflectionPolicy(max_rounds=1),
        tracker,
    )
    window = RequestBudgetWindow.start(now=0.0, max_seconds=2)
    state = {
        "messages": [
            AIMessage(
                content="",
                name="primary",
                tool_calls=[
                    {
                        "name": "slow_tool",
                        "args": {"value": "ok"},
                        "id": "call-slow",
                    }
                ],
            )
        ]
    }

    update = node.invoke(
        state,
        {
            "metadata": {
                REQUEST_BUDGET_METADATA_KEY: window.to_metadata(),
            }
        },
    )

    assert calls == ["ok"]
    assert update["budget_status"] == "terminating"
    assert update["budget_termination"]["scope"] == "request"
    assert update["budget_termination"]["phase"] == "after"
    assert BudgetUsage.from_state(update["budget_usage"]).tool_calls == 1


def test_enabled_request_budget_rejects_graph_invocation_without_runtime_window():
    tracker = _limited_tracker(ExecutionBudget(request_max_seconds=2))
    node = budgeted_request_start_node(
        lambda state, config: {"user_info": "profile"},
        tracker,
    )

    with pytest.raises(ValidationError) as exc_info:
        node({"messages": []}, None)

    assert exc_info.value.code == "request_budget_missing"


def test_compiled_graph_routes_expired_request_to_deterministic_terminal_node(
    graph_spec,
):
    budget = ExecutionBudget(request_max_seconds=1)
    tracker = WorkflowBudgetTracker(
        ModelPriceTable.empty(),
        execution_budget=budget,
        wall_clock=lambda: datetime(2026, 7, 12, tzinfo=UTC),
        monotonic_clock=lambda: 2.0,
        event_logger=lambda event, **fields: None,
    )
    spec = replace(
        graph_spec,
        primary=replace(
            graph_spec.primary,
            assistant=Assistant(
                SequencedRunnable([_message("must not run", 1, 1)]),
                name="primary",
            ),
        ),
        execution_policy=replace(graph_spec.execution_policy, budget=budget),
        budget_tracker=tracker,
    )
    graph = build_multi_agentic_graph(MemorySaver(), spec)
    window = RequestBudgetWindow.start(now=0.0, max_seconds=1)
    config = {
        "configurable": {"thread_id": "budget-expired"},
        "metadata": {REQUEST_BUDGET_METADATA_KEY: window.to_metadata()},
    }

    list(
        graph.stream(
            {
                "messages": [("user", "Explain RAG")],
                "user_id": "default",
                "namespace": "tech_docs",
            },
            config,
            stream_mode="updates",
        )
    )
    snapshot = graph.get_state(config)

    assert snapshot.next == ()
    assert snapshot.values["budget_status"] == "terminated"
    assert snapshot.values["budget_termination"]["scope"] == "request"
    assert snapshot.values["budget_usage"]["llm_calls"] == 0
    assert "安全停止" in snapshot.values["messages"][-1].content
