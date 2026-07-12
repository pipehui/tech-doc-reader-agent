from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Literal

from tech_doc_agent.app.core.budget import BudgetUsage
from tech_doc_agent.app.core.execution_budget_models import (
    REQUEST_BUDGET_METADATA_KEY,
    BudgetDecision,
    BudgetDimension,
    BudgetOperation,
    BudgetPhase,
    BudgetReason,
    BudgetScope,
    BudgetValue,
    ExecutionBudgetExceeded,
    RequestBudgetWindow,
)
from tech_doc_agent.app.core.settings import Settings


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    request_max_seconds: float | None = None
    workflow_max_llm_calls: int | None = None
    workflow_max_tool_calls: int | None = None
    workflow_max_total_tokens: int | None = None
    workflow_max_estimated_cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if self.request_max_seconds is not None:
            _positive_finite(self.request_max_seconds, "request_max_seconds")
        for field_name, value in (
            ("workflow_max_llm_calls", self.workflow_max_llm_calls),
            ("workflow_max_tool_calls", self.workflow_max_tool_calls),
            ("workflow_max_total_tokens", self.workflow_max_total_tokens),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(f"{field_name} must be greater than zero or None")
        if self.workflow_max_estimated_cost_usd is not None:
            cost_limit = self.workflow_max_estimated_cost_usd
            if (
                not isinstance(cost_limit, Decimal)
                or not cost_limit.is_finite()
                or cost_limit <= 0
            ):
                raise ValueError(
                    "workflow_max_estimated_cost_usd must be greater than zero or None"
                )

    def start_request(self, *, now: float) -> RequestBudgetWindow | None:
        if self.request_max_seconds is None:
            return None
        return RequestBudgetWindow.start(now=now, max_seconds=self.request_max_seconds)

    def check_before(
        self,
        usage: BudgetUsage,
        *,
        operation: BudgetOperation,
        now: float,
        request_window: RequestBudgetWindow | None,
        additional_calls: int = 1,
        phase: Literal["before", "resume"] = "before",
    ) -> BudgetDecision | None:
        if (
            isinstance(additional_calls, bool)
            or not isinstance(additional_calls, int)
            or additional_calls < 1
        ):
            raise ValueError("additional_calls must be greater than zero")
        request_decision = self._request_decision(
            now=now,
            request_window=request_window,
            phase=phase,
            operation=operation,
        )
        if request_decision is not None:
            return request_decision

        if operation == "tool":
            return self._before_tool_calls(
                usage,
                additional_calls=additional_calls,
                phase=phase,
            )
        return self._before_llm(usage, additional_calls=additional_calls, phase=phase)

    def check_after(
        self,
        usage: BudgetUsage,
        *,
        operation: BudgetOperation,
        now: float,
        request_window: RequestBudgetWindow | None,
    ) -> BudgetDecision | None:
        request_decision = self._request_decision(
            now=now,
            request_window=request_window,
            phase="after",
            operation=operation,
        )
        if request_decision is not None:
            return request_decision

        if operation == "tool":
            if (
                self.workflow_max_tool_calls is not None
                and usage.tool_calls > self.workflow_max_tool_calls
            ):
                return _workflow_decision(
                    dimension="tool_calls",
                    phase="after",
                    operation=operation,
                    reason="limit_exceeded",
                    observed=usage.tool_calls,
                    limit=self.workflow_max_tool_calls,
                )
            return None
        return self._after_llm(usage)

    def _before_tool_calls(
        self,
        usage: BudgetUsage,
        *,
        additional_calls: int,
        phase: Literal["before", "resume"],
    ) -> BudgetDecision | None:
        if self.workflow_max_tool_calls is None:
            return None
        projected = usage.tool_calls + additional_calls
        if projected <= self.workflow_max_tool_calls:
            return None
        return _workflow_decision(
            dimension="tool_calls",
            phase=phase,
            operation="tool",
            reason="limit_would_be_exceeded",
            observed=projected,
            limit=self.workflow_max_tool_calls,
        )

    def _before_llm(
        self,
        usage: BudgetUsage,
        *,
        additional_calls: int,
        phase: Literal["before", "resume"],
    ) -> BudgetDecision | None:
        if self.workflow_max_llm_calls is not None:
            projected = usage.llm_calls + additional_calls
            if projected > self.workflow_max_llm_calls:
                return _workflow_decision(
                    dimension="llm_calls",
                    phase=phase,
                    operation="llm",
                    reason="limit_would_be_exceeded",
                    observed=projected,
                    limit=self.workflow_max_llm_calls,
                )

        if self.workflow_max_total_tokens is not None:
            if usage.total_tokens is None:
                return _workflow_decision(
                    dimension="total_tokens",
                    phase=phase,
                    operation="llm",
                    reason="usage_unreported",
                    observed=None,
                    limit=self.workflow_max_total_tokens,
                )
            if usage.total_tokens >= self.workflow_max_total_tokens:
                return _workflow_decision(
                    dimension="total_tokens",
                    phase=phase,
                    operation="llm",
                    reason="limit_would_be_exceeded",
                    observed=usage.total_tokens,
                    limit=self.workflow_max_total_tokens,
                )

        if self.workflow_max_estimated_cost_usd is not None:
            cost_limit = str(self.workflow_max_estimated_cost_usd)
            if usage.estimated_cost_usd is None:
                return _workflow_decision(
                    dimension="estimated_cost_usd",
                    phase=phase,
                    operation="llm",
                    reason="usage_unreported",
                    observed=None,
                    limit=cost_limit,
                )
            if usage.estimated_cost_usd >= self.workflow_max_estimated_cost_usd:
                return _workflow_decision(
                    dimension="estimated_cost_usd",
                    phase=phase,
                    operation="llm",
                    reason="limit_would_be_exceeded",
                    observed=str(usage.estimated_cost_usd),
                    limit=cost_limit,
                )
        return None

    def _after_llm(self, usage: BudgetUsage) -> BudgetDecision | None:
        checks: tuple[tuple[BudgetDimension, BudgetValue, BudgetValue], ...] = (
            ("llm_calls", usage.llm_calls, self.workflow_max_llm_calls),
            ("total_tokens", usage.total_tokens, self.workflow_max_total_tokens),
            (
                "estimated_cost_usd",
                str(usage.estimated_cost_usd)
                if usage.estimated_cost_usd is not None
                else None,
                str(self.workflow_max_estimated_cost_usd)
                if self.workflow_max_estimated_cost_usd is not None
                else None,
            ),
        )
        for dimension, observed, limit in checks:
            if observed is None or limit is None:
                continue
            if Decimal(str(observed)) > Decimal(str(limit)):
                return _workflow_decision(
                    dimension=dimension,
                    phase="after",
                    operation="llm",
                    reason="limit_exceeded",
                    observed=observed,
                    limit=limit,
                )
        return None

    def _request_decision(
        self,
        *,
        now: float,
        request_window: RequestBudgetWindow | None,
        phase: BudgetPhase,
        operation: BudgetOperation,
    ) -> BudgetDecision | None:
        if self.request_max_seconds is None or request_window is None:
            return None
        _finite(now, "now")
        effective_limit = min(self.request_max_seconds, request_window.max_seconds)
        effective_deadline = request_window.started_monotonic + effective_limit
        if now < effective_deadline:
            return None
        return BudgetDecision(
            schema_version=1,
            scope="request",
            dimension="elapsed_seconds",
            phase=phase,
            operation=operation,
            reason="request_deadline_exceeded",
            observed=round(max(0.0, now - request_window.started_monotonic), 6),
            limit=effective_limit,
        )


def build_execution_budget(settings: Settings) -> ExecutionBudget:
    return ExecutionBudget(
        request_max_seconds=_enabled_float(settings.REQUEST_MAX_SECONDS),
        workflow_max_llm_calls=_enabled_int(settings.WORKFLOW_MAX_LLM_CALLS),
        workflow_max_tool_calls=_enabled_int(settings.WORKFLOW_MAX_TOOL_CALLS),
        workflow_max_total_tokens=_enabled_int(settings.WORKFLOW_MAX_TOTAL_TOKENS),
        workflow_max_estimated_cost_usd=_enabled_decimal(
            settings.WORKFLOW_MAX_ESTIMATED_COST_USD
        ),
    )


def _workflow_decision(
    *,
    dimension: BudgetDimension,
    phase: BudgetPhase,
    operation: BudgetOperation,
    reason: BudgetReason,
    observed: BudgetValue,
    limit: BudgetValue,
) -> BudgetDecision:
    return BudgetDecision(
        schema_version=1,
        scope="workflow",
        dimension=dimension,
        phase=phase,
        operation=operation,
        reason=reason,
        observed=observed,
        limit=limit,
    )


def _enabled_int(value: int) -> int | None:
    return value or None


def _enabled_float(value: float) -> float | None:
    return value or None


def _enabled_decimal(value: Decimal) -> Decimal | None:
    return value or None


def _finite(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field_name} must be finite")


def _positive_finite(value: float, field_name: str) -> None:
    _finite(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero")


__all__ = [
    "BudgetDecision",
    "BudgetDimension",
    "BudgetOperation",
    "BudgetPhase",
    "BudgetReason",
    "BudgetScope",
    "ExecutionBudget",
    "ExecutionBudgetExceeded",
    "REQUEST_BUDGET_METADATA_KEY",
    "RequestBudgetWindow",
    "build_execution_budget",
]
