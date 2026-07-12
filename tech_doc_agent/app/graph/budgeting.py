from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tech_doc_agent.app.core.budget import BudgetUsage, LlmUsage
from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tech_doc_agent.app.core.observability import log_event

from .state import State


@dataclass(slots=True)
class WorkflowBudgetTracker:
    price_table: ModelPriceTable
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    event_logger: Callable[..., None] = log_event

    def current(self, state: State) -> BudgetUsage:
        payload = state.get("budget_usage")
        if payload is None:
            return BudgetUsage.new(now=self.clock())
        return BudgetUsage.from_state(payload)

    def record_assistant(
        self,
        state: State,
        assistant_update: dict[str, Any],
        *,
        current: BudgetUsage | None = None,
    ) -> dict[str, Any]:
        update = dict(assistant_update)
        raw_usages = update.pop("_llm_usage", ())
        if not isinstance(raw_usages, (list, tuple)) or any(
            not isinstance(usage, LlmUsage) for usage in raw_usages
        ):
            raise ValidationError(
                "The assistant returned invalid budget usage metadata.",
                code="llm_usage_invalid",
                dependency="llm",
                cause_type=type(raw_usages).__name__,
        )

        usage = current or self.current(state)
        if not raw_usages:
            return {
                **update,
                "budget_usage": usage.to_state(),
                "budget_usage_delta": {},
            }
        llm_calls_delta = 0
        reported_input_tokens = 0
        reported_output_tokens = 0
        reported_total_tokens = 0
        unreported_input_token_calls = 0
        unreported_output_token_calls = 0
        unreported_total_token_calls = 0
        priced_cost_usd = Decimal("0")
        unpriced_llm_calls = 0
        for delta in raw_usages:
            usage, estimate = usage.record_llm(delta, self.price_table)
            llm_calls_delta += delta.calls
            reported_input_tokens += delta.input_tokens or 0
            reported_output_tokens += delta.output_tokens or 0
            reported_total_tokens += delta.total_tokens or 0
            unreported_input_token_calls += delta.calls if delta.input_tokens is None else 0
            unreported_output_token_calls += delta.calls if delta.output_tokens is None else 0
            unreported_total_token_calls += delta.calls if delta.total_tokens is None else 0
            if estimate.estimated_cost_usd is None:
                unpriced_llm_calls += delta.calls
            else:
                priced_cost_usd += estimate.estimated_cost_usd
            self.event_logger(
                "budget.usage.llm",
                provider=delta.provider,
                model=delta.model,
                llm_calls_delta=delta.calls,
                input_tokens_delta=delta.input_tokens,
                output_tokens_delta=delta.output_tokens,
                total_tokens_delta=delta.total_tokens,
                estimated_cost_usd_delta=(
                    float(estimate.estimated_cost_usd)
                    if estimate.estimated_cost_usd is not None
                    else None
                ),
                price_table_version=estimate.price_table_version,
                price_version=estimate.price_version,
                price_effective_at=estimate.price_effective_at,
                pricing_reason=estimate.reason,
                llm_calls=usage.llm_calls,
                tool_calls=usage.tool_calls,
                total_tokens=usage.total_tokens,
                estimated_cost_usd=(
                    float(usage.estimated_cost_usd)
                    if usage.estimated_cost_usd is not None
                    else None
                ),
            )
        return {
            **update,
            "budget_usage": usage.to_state(),
            "budget_usage_delta": {
                "kind": "llm",
                "llm_calls": llm_calls_delta,
                "tool_calls": 0,
                "input_tokens": (
                    None if unreported_input_token_calls else reported_input_tokens
                ),
                "output_tokens": (
                    None if unreported_output_token_calls else reported_output_tokens
                ),
                "total_tokens": (
                    None if unreported_total_token_calls else reported_total_tokens
                ),
                "reported_input_tokens": reported_input_tokens,
                "reported_output_tokens": reported_output_tokens,
                "reported_total_tokens": reported_total_tokens,
                "unreported_input_token_calls": unreported_input_token_calls,
                "unreported_output_token_calls": unreported_output_token_calls,
                "unreported_total_token_calls": unreported_total_token_calls,
                "estimated_cost_usd": (
                    None if unpriced_llm_calls else float(priced_cost_usd)
                ),
                "priced_cost_usd": str(priced_cost_usd),
                "unpriced_llm_calls": unpriced_llm_calls,
            },
        }

    def record_tools(
        self,
        state: State,
        tool_update: dict[str, Any],
        *,
        calls: int,
    ) -> dict[str, Any]:
        usage = self.current(state).record_tools(calls)
        self.event_logger(
            "budget.usage.tool",
            tool_calls_delta=calls,
            llm_calls=usage.llm_calls,
            tool_calls=usage.tool_calls,
            total_tokens=usage.total_tokens,
            estimated_cost_usd=(
                float(usage.estimated_cost_usd)
                if usage.estimated_cost_usd is not None
                else None
            ),
        )
        return {
            **tool_update,
            "budget_usage": usage.to_state(),
            "budget_usage_delta": {
                "kind": "tool",
                "llm_calls": 0,
                "tool_calls": calls,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "reported_input_tokens": 0,
                "reported_output_tokens": 0,
                "reported_total_tokens": 0,
                "unreported_input_token_calls": 0,
                "unreported_output_token_calls": 0,
                "unreported_total_token_calls": 0,
                "estimated_cost_usd": 0.0,
                "priced_cost_usd": "0",
                "unpriced_llm_calls": 0,
            },
        }


def budgeted_request_start_node(
    node: Callable,
    tracker: WorkflowBudgetTracker,
) -> Callable:
    def invoke(state: State, config=None):
        started = BudgetUsage.new(now=tracker.clock())
        update = node(state, config)
        return {
            **update,
            "budget_usage": started.to_state(),
            "budget_usage_delta": {},
        }

    return invoke


__all__ = ["WorkflowBudgetTracker", "budgeted_request_start_node"]
