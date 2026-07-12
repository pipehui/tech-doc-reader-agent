from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import Any, Literal

from tech_doc_agent.app.core.budget import BudgetUsage, LlmUsage
from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.execution_budget import (
    BudgetDecision,
    ExecutionBudget,
    ExecutionBudgetExceeded,
    RequestBudgetWindow,
)
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tech_doc_agent.app.core.observability import log_event

from .budget_termination import (
    budget_closed_tool_messages,
    last_ai_tool_calls,
    mark_budget_terminating,
    update_messages,
)
from .state import State


@dataclass(slots=True)
class WorkflowBudgetTracker:
    price_table: ModelPriceTable
    execution_budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    monotonic_clock: Callable[[], float] = monotonic
    event_logger: Callable[..., None] = log_event

    def current(self, state: State) -> BudgetUsage:
        payload = state.get("budget_usage")
        if payload is None:
            return BudgetUsage.new(now=self.wall_clock())
        return BudgetUsage.from_state(payload)

    def assert_before_llm_attempt(
        self,
        state: State,
        config: Any,
        *,
        current: BudgetUsage,
        local_usages: tuple[LlmUsage, ...],
    ) -> None:
        projected = self._apply_llm_usages(current, local_usages)
        decision = self.execution_budget.check_before(
            projected,
            operation="llm",
            now=self.monotonic_clock(),
            request_window=self._request_window(config),
            additional_calls=1,
            phase="before",
        )
        if decision is None:
            return
        self._log_blocked(decision, projected)
        raise ExecutionBudgetExceeded(decision)

    def block_tools_before_execution(
        self,
        state: State,
        config: Any,
        *,
        calls: int,
    ) -> dict[str, Any] | None:
        if calls < 1:
            return None
        usage = self.current(state)
        decision = self.execution_budget.check_before(
            usage,
            operation="tool",
            now=self.monotonic_clock(),
            request_window=self._request_window(config),
            additional_calls=calls,
            phase=_before_phase(config),
        )
        if decision is None:
            return None
        self._log_blocked(decision, usage)
        update: dict[str, Any] = {
            "messages": budget_closed_tool_messages(
                last_ai_tool_calls(state.get("messages", [])),
                decision,
            ),
            "budget_usage": usage.to_state(),
            "budget_usage_delta": {},
        }
        return mark_budget_terminating(update, decision)

    def record_assistant(
        self,
        state: State,
        assistant_update: dict[str, Any],
        *,
        config: Any = None,
        current: BudgetUsage | None = None,
    ) -> dict[str, Any]:
        update = dict(assistant_update)
        raw_usages = update.pop("_llm_usage", ())
        raw_decision = update.pop("_budget_decision", None)
        if not isinstance(raw_usages, (list, tuple)) or any(
            not isinstance(usage, LlmUsage) for usage in raw_usages
        ):
            raise ValidationError(
                "The assistant returned invalid budget usage metadata.",
                code="llm_usage_invalid",
                dependency="llm",
                cause_type=type(raw_usages).__name__,
            )
        if raw_decision is not None and not isinstance(raw_decision, BudgetDecision):
            raise ValidationError(
                "The assistant returned an invalid execution budget decision.",
                code="budget_decision_invalid",
                dependency="llm",
                cause_type=type(raw_decision).__name__,
            )

        usage = current or self.current(state)
        delta = _LlmUsageDelta()
        for item in raw_usages:
            usage, estimate = usage.record_llm(item, self.price_table)
            delta.record(item, estimate.estimated_cost_usd)
            self.event_logger(
                "budget.usage.llm",
                provider=item.provider,
                model=item.model,
                llm_calls_delta=item.calls,
                input_tokens_delta=item.input_tokens,
                output_tokens_delta=item.output_tokens,
                total_tokens_delta=item.total_tokens,
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

        update = {
            **update,
            "budget_usage": usage.to_state(),
            "budget_usage_delta": delta.to_state() if raw_usages else {},
        }
        decision = raw_decision or self.execution_budget.check_after(
            usage,
            operation="llm",
            now=self.monotonic_clock(),
            request_window=self._request_window(config),
        )
        if decision is None:
            return update
        if raw_decision is None:
            self._log_blocked(decision, usage)
        tool_calls = last_ai_tool_calls(update_messages(update))
        if tool_calls:
            update["messages"] = [
                *update_messages(update),
                *budget_closed_tool_messages(tool_calls, decision),
            ]
        return mark_budget_terminating(update, decision)

    def record_tools(
        self,
        state: State,
        tool_update: dict[str, Any],
        *,
        calls: int,
        config: Any = None,
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
        update = {
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
        decision = self.execution_budget.check_after(
            usage,
            operation="tool",
            now=self.monotonic_clock(),
            request_window=self._request_window(config),
        )
        if decision is None:
            return update
        self._log_blocked(decision, usage)
        return mark_budget_terminating(update, decision)

    def validate_request_config(self, config: Any) -> None:
        self._request_window(config)

    def _request_window(self, config: Any) -> RequestBudgetWindow | None:
        window = RequestBudgetWindow.from_config(config)
        if self.execution_budget.request_max_seconds is not None and window is None:
            raise ValidationError(
                "The request execution budget metadata is missing.",
                code="request_budget_missing",
                dependency="runtime_config",
                cause_type="RequestBudgetConfiguration",
            )
        return window

    def _apply_llm_usages(
        self,
        usage: BudgetUsage,
        usages: Iterable[LlmUsage],
    ) -> BudgetUsage:
        for item in usages:
            usage, _ = usage.record_llm(item, self.price_table)
        return usage

    def _log_blocked(self, decision: BudgetDecision, usage: BudgetUsage) -> None:
        self.event_logger(
            "budget.check.blocked",
            scope=decision.scope,
            dimension=decision.dimension,
            phase=decision.phase,
            operation=decision.operation,
            reason=decision.reason,
            observed=decision.observed,
            limit=decision.limit,
            llm_calls=usage.llm_calls,
            tool_calls=usage.tool_calls,
            total_tokens=usage.total_tokens,
            estimated_cost_usd=(
                float(usage.estimated_cost_usd)
                if usage.estimated_cost_usd is not None
                else None
            ),
        )


@dataclass(slots=True)
class _LlmUsageDelta:
    llm_calls: int = 0
    reported_input_tokens: int = 0
    reported_output_tokens: int = 0
    reported_total_tokens: int = 0
    unreported_input_token_calls: int = 0
    unreported_output_token_calls: int = 0
    unreported_total_token_calls: int = 0
    priced_cost_usd: Decimal = Decimal("0")
    unpriced_llm_calls: int = 0

    def record(self, usage: LlmUsage, estimated_cost_usd: Decimal | None) -> None:
        self.llm_calls += usage.calls
        self.reported_input_tokens += usage.input_tokens or 0
        self.reported_output_tokens += usage.output_tokens or 0
        self.reported_total_tokens += usage.total_tokens or 0
        self.unreported_input_token_calls += (
            usage.calls if usage.input_tokens is None else 0
        )
        self.unreported_output_token_calls += (
            usage.calls if usage.output_tokens is None else 0
        )
        self.unreported_total_token_calls += (
            usage.calls if usage.total_tokens is None else 0
        )
        if estimated_cost_usd is None:
            self.unpriced_llm_calls += usage.calls
        else:
            self.priced_cost_usd += estimated_cost_usd

    def to_state(self) -> dict[str, Any]:
        return {
            "kind": "llm",
            "llm_calls": self.llm_calls,
            "tool_calls": 0,
            "input_tokens": (
                None
                if self.unreported_input_token_calls
                else self.reported_input_tokens
            ),
            "output_tokens": (
                None
                if self.unreported_output_token_calls
                else self.reported_output_tokens
            ),
            "total_tokens": (
                None
                if self.unreported_total_token_calls
                else self.reported_total_tokens
            ),
            "reported_input_tokens": self.reported_input_tokens,
            "reported_output_tokens": self.reported_output_tokens,
            "reported_total_tokens": self.reported_total_tokens,
            "unreported_input_token_calls": self.unreported_input_token_calls,
            "unreported_output_token_calls": self.unreported_output_token_calls,
            "unreported_total_token_calls": self.unreported_total_token_calls,
            "estimated_cost_usd": (
                None
                if self.unpriced_llm_calls
                else float(self.priced_cost_usd)
            ),
            "priced_cost_usd": str(self.priced_cost_usd),
            "unpriced_llm_calls": self.unpriced_llm_calls,
        }


def budgeted_request_start_node(
    node: Callable,
    tracker: WorkflowBudgetTracker,
) -> Callable:
    def invoke(state: State, config=None):
        tracker.validate_request_config(config)
        started = BudgetUsage.new(now=tracker.wall_clock())
        update = node(state, config)
        return {
            **update,
            "budget_usage": started.to_state(),
            "budget_usage_delta": {},
            "budget_status": "active",
            "budget_termination": {},
        }

    return invoke


def _before_phase(config: Any) -> Literal["before", "resume"]:
    if not isinstance(config, dict):
        return "before"
    metadata = config.get("metadata")
    if isinstance(metadata, dict) and metadata.get("runtime_operation") == "approval":
        return "resume"
    return "before"


__all__ = [
    "WorkflowBudgetTracker",
    "budgeted_request_start_node",
]
