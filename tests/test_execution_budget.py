from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tech_doc_agent.app.core.budget import BudgetUsage, LlmUsage
from tech_doc_agent.app.core.errors import ValidationError, classify_error
from tech_doc_agent.app.core.execution_budget import (
    REQUEST_BUDGET_METADATA_KEY,
    BudgetDecision,
    ExecutionBudget,
    ExecutionBudgetExceeded,
    RequestBudgetWindow,
    build_execution_budget,
)
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tech_doc_agent.app.core.settings import Settings
from tests.test_model_pricing import PRICE_TABLE_PAYLOAD


def _usage() -> BudgetUsage:
    return BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC))


def _recorded_usage(
    *,
    calls: int = 1,
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
    total_tokens: int | None = 15,
    model: str | None = "test-model",
) -> BudgetUsage:
    usage, _ = _usage().record_llm(
        LlmUsage(
            calls=calls,
            provider="openai_compatible",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        ),
        ModelPriceTable.from_payload(PRICE_TABLE_PAYLOAD),
    )
    return usage


def test_settings_zero_disables_optional_dimensions_and_keeps_call_caps():
    budget = build_execution_budget(
        Settings(
            REQUEST_MAX_SECONDS=0,
            WORKFLOW_MAX_LLM_CALLS=4,
            WORKFLOW_MAX_TOOL_CALLS=5,
            WORKFLOW_MAX_TOTAL_TOKENS=0,
            WORKFLOW_MAX_ESTIMATED_COST_USD=0,
        )
    )

    assert budget.request_max_seconds is None
    assert budget.workflow_max_llm_calls == 4
    assert budget.workflow_max_tool_calls == 5
    assert budget.workflow_max_total_tokens is None
    assert budget.workflow_max_estimated_cost_usd is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"request_max_seconds": True},
        {"workflow_max_llm_calls": 1.5},
        {"workflow_max_tool_calls": 0},
        {"workflow_max_total_tokens": -1},
        {"workflow_max_estimated_cost_usd": Decimal("nan")},
    ],
)
def test_execution_budget_rejects_ambiguous_direct_limits(kwargs):
    with pytest.raises(ValueError):
        ExecutionBudget(**kwargs)


def test_request_window_round_trips_only_through_runtime_metadata():
    window = RequestBudgetWindow.start(now=20.0, max_seconds=3.5)
    config = {"metadata": {REQUEST_BUDGET_METADATA_KEY: window.to_metadata()}}

    assert RequestBudgetWindow.from_config(config) == window
    assert "started_monotonic" not in _usage().to_state()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": True},
        {"schema_version": 2},
        {
            "schema_version": 1,
            "started_monotonic": 2.0,
            "deadline_monotonic": 1.0,
            "max_seconds": 1.0,
        },
        {
            "schema_version": 1,
            "started_monotonic": 1.0,
            "deadline_monotonic": 3.0,
            "max_seconds": 1.0,
        },
    ],
)
def test_request_window_rejects_corrupt_internal_metadata(payload):
    with pytest.raises(ValidationError) as exc_info:
        RequestBudgetWindow.from_config(
            {"metadata": {REQUEST_BUDGET_METADATA_KEY: payload}}
        )

    assert exc_info.value.code == "request_budget_invalid"


def test_before_llm_uses_projected_call_count_without_consuming_usage():
    budget = ExecutionBudget(workflow_max_llm_calls=2)
    usage = _recorded_usage(calls=2)

    decision = budget.check_before(
        usage,
        operation="llm",
        now=0.0,
        request_window=None,
    )

    assert decision is not None
    assert decision.dimension == "llm_calls"
    assert decision.reason == "limit_would_be_exceeded"
    assert decision.observed == 3
    assert usage.llm_calls == 2


def test_tool_batch_is_all_or_none_when_projected_count_exceeds_limit():
    budget = ExecutionBudget(workflow_max_tool_calls=3)
    usage = _usage().record_tools(2)

    decision = budget.check_before(
        usage,
        operation="tool",
        now=0.0,
        request_window=None,
        additional_calls=2,
        phase="resume",
    )

    assert decision is not None
    assert decision.dimension == "tool_calls"
    assert decision.phase == "resume"
    assert decision.observed == 4


def test_unknown_token_or_cost_blocks_only_the_next_llm_when_cap_is_enabled():
    usage = _recorded_usage(
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        model=None,
    )
    budget = ExecutionBudget(
        workflow_max_total_tokens=100,
        workflow_max_estimated_cost_usd=Decimal("1"),
    )

    assert (
        budget.check_after(
            usage,
            operation="llm",
            now=0.0,
            request_window=None,
        )
        is None
    )
    tool_decision = budget.check_before(
        usage,
        operation="tool",
        now=0.0,
        request_window=None,
    )
    llm_decision = budget.check_before(
        usage,
        operation="llm",
        now=0.0,
        request_window=None,
    )

    assert tool_decision is None
    assert llm_decision is not None
    assert llm_decision.dimension == "total_tokens"
    assert llm_decision.reason == "usage_unreported"
    assert llm_decision.observed is None


def test_after_check_stops_only_on_actual_overshoot_not_exact_boundary():
    exact = ExecutionBudget(workflow_max_total_tokens=15)
    exceeded = ExecutionBudget(workflow_max_total_tokens=14)
    usage = _recorded_usage(total_tokens=15)

    assert (
        exact.check_after(
            usage,
            operation="llm",
            now=0.0,
            request_window=None,
        )
        is None
    )
    decision = exceeded.check_after(
        usage,
        operation="llm",
        now=0.0,
        request_window=None,
    )

    assert decision is not None
    assert decision.reason == "limit_exceeded"
    assert decision.observed == 15


def test_request_deadline_is_checked_before_and_after_atomic_operations():
    budget = ExecutionBudget(request_max_seconds=2.0)
    window = budget.start_request(now=10.0)
    assert window is not None

    assert (
        budget.check_before(
            _usage(),
            operation="llm",
            now=11.999,
            request_window=window,
        )
        is None
    )
    decision = budget.check_after(
        _usage(),
        operation="tool",
        now=12.0,
        request_window=window,
    )

    assert decision is not None
    assert decision.scope == "request"
    assert decision.dimension == "elapsed_seconds"
    assert decision.observed == 2.0


def test_budget_decision_round_trip_and_error_classification_preserve_policy():
    decision = BudgetDecision(
        schema_version=1,
        scope="workflow",
        dimension="llm_calls",
        phase="before",
        operation="llm",
        reason="limit_would_be_exceeded",
        observed=4,
        limit=3,
    )

    restored = BudgetDecision.from_state(decision.to_state())
    mapped = classify_error(ExecutionBudgetExceeded(decision), dependency="llm")

    assert restored == decision
    assert isinstance(mapped, ExecutionBudgetExceeded)
    assert mapped.decision == decision
    assert mapped.code == "execution_budget_exceeded"
    assert mapped.retryable is False
