from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Literal

from tech_doc_agent.app.core.errors import ApplicationError, ValidationError


REQUEST_BUDGET_METADATA_KEY = "execution_budget_request"

BudgetScope = Literal["request", "workflow"]
BudgetDimension = Literal[
    "elapsed_seconds",
    "llm_calls",
    "tool_calls",
    "total_tokens",
    "estimated_cost_usd",
]
BudgetPhase = Literal["before", "after", "resume"]
BudgetOperation = Literal["llm", "tool"]
BudgetReason = Literal[
    "request_deadline_exceeded",
    "limit_would_be_exceeded",
    "limit_exceeded",
    "usage_unreported",
]
BudgetValue = int | float | str | None


@dataclass(frozen=True, slots=True)
class RequestBudgetWindow:
    """Process-local request timing data that must never enter checkpoint state."""

    schema_version: int
    started_monotonic: float
    deadline_monotonic: float
    max_seconds: float

    @classmethod
    def start(cls, *, now: float, max_seconds: float) -> RequestBudgetWindow:
        _positive_finite(max_seconds, "max_seconds")
        _finite(now, "now")
        return cls(
            schema_version=1,
            started_monotonic=now,
            deadline_monotonic=now + max_seconds,
            max_seconds=max_seconds,
        )

    @classmethod
    def from_config(cls, config: Any) -> RequestBudgetWindow | None:
        if not isinstance(config, dict):
            return None
        metadata = config.get("metadata")
        if not isinstance(metadata, dict):
            return None
        payload = metadata.get(REQUEST_BUDGET_METADATA_KEY)
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise _invalid_request_window()
        schema_version = payload.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 1
        ):
            raise _invalid_request_window()
        try:
            started = float(payload["started_monotonic"])
            deadline = float(payload["deadline_monotonic"])
            max_seconds = float(payload["max_seconds"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise _invalid_request_window() from None
        _finite_or_invalid(started)
        _finite_or_invalid(deadline)
        _positive_finite_or_invalid(max_seconds)
        if deadline < started or abs((deadline - started) - max_seconds) > 1e-6:
            raise _invalid_request_window()
        return cls(
            schema_version=1,
            started_monotonic=started,
            deadline_monotonic=deadline,
            max_seconds=max_seconds,
        )

    def to_metadata(self) -> dict[str, int | float]:
        return {
            "schema_version": self.schema_version,
            "started_monotonic": self.started_monotonic,
            "deadline_monotonic": self.deadline_monotonic,
            "max_seconds": self.max_seconds,
        }


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    schema_version: int
    scope: BudgetScope
    dimension: BudgetDimension
    phase: BudgetPhase
    operation: BudgetOperation
    reason: BudgetReason
    observed: BudgetValue
    limit: BudgetValue

    @classmethod
    def from_state(cls, payload: Any) -> BudgetDecision:
        if not isinstance(payload, dict):
            raise _invalid_budget_decision()
        schema_version = payload.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 1
        ):
            raise _invalid_budget_decision()
        scope = payload.get("scope")
        dimension = payload.get("dimension")
        phase = payload.get("phase")
        operation = payload.get("operation")
        reason = payload.get("reason")
        if scope not in {"request", "workflow"}:
            raise _invalid_budget_decision()
        if dimension not in {
            "elapsed_seconds",
            "llm_calls",
            "tool_calls",
            "total_tokens",
            "estimated_cost_usd",
        }:
            raise _invalid_budget_decision()
        if phase not in {"before", "after", "resume"}:
            raise _invalid_budget_decision()
        if operation not in {"llm", "tool"}:
            raise _invalid_budget_decision()
        if reason not in {
            "request_deadline_exceeded",
            "limit_would_be_exceeded",
            "limit_exceeded",
            "usage_unreported",
        }:
            raise _invalid_budget_decision()
        return cls(
            schema_version=1,
            scope=scope,
            dimension=dimension,
            phase=phase,
            operation=operation,
            reason=reason,
            observed=_budget_value(payload.get("observed")),
            limit=_budget_value(payload.get("limit")),
        )

    @property
    def error_code(self) -> str:
        return (
            "execution_budget_usage_unreported"
            if self.reason == "usage_unreported"
            else "execution_budget_exceeded"
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope,
            "dimension": self.dimension,
            "phase": self.phase,
            "operation": self.operation,
            "reason": self.reason,
            "observed": self.observed,
            "limit": self.limit,
        }


class ExecutionBudgetExceeded(ApplicationError):
    default_code = "execution_budget_exceeded"
    default_safe_message = "The execution budget does not allow another operation."

    def __init__(
        self,
        decision: BudgetDecision,
        *,
        dependency: str | None = "execution_budget",
        tool: str | None = None,
    ) -> None:
        self.decision = decision
        super().__init__(
            code=decision.error_code,
            dependency=dependency,
            tool=tool,
            cause_type="ExecutionBudgetPolicy",
        )

    def with_context(
        self,
        *,
        dependency: str | None = None,
        tool: str | None = None,
    ) -> ExecutionBudgetExceeded:
        return ExecutionBudgetExceeded(
            self.decision,
            dependency=self.dependency or dependency,
            tool=self.tool or tool,
        )


def _budget_value(value: Any) -> BudgetValue:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _invalid_budget_decision()
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            raise _invalid_budget_decision() from None
        if not parsed.is_finite() or parsed < 0:
            raise _invalid_budget_decision()
    elif isinstance(value, float):
        if not math.isfinite(value) or value < 0:
            raise _invalid_budget_decision()
    elif value < 0:
        raise _invalid_budget_decision()
    return value


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


def _finite_or_invalid(value: float) -> None:
    try:
        _finite(value, "request budget value")
    except ValueError:
        raise _invalid_request_window() from None


def _positive_finite_or_invalid(value: float) -> None:
    try:
        _positive_finite(value, "request budget value")
    except ValueError:
        raise _invalid_request_window() from None


def _invalid_request_window() -> ValidationError:
    return ValidationError(
        "The request execution budget metadata is invalid.",
        code="request_budget_invalid",
        dependency="runtime_config",
        cause_type="RequestBudgetValidation",
    )


def _invalid_budget_decision() -> ValidationError:
    return ValidationError(
        "The persisted execution budget decision is invalid.",
        code="budget_decision_invalid",
        dependency="workflow_state",
        cause_type="BudgetDecisionValidation",
    )


__all__ = [
    "BudgetDecision",
    "BudgetDimension",
    "BudgetOperation",
    "BudgetPhase",
    "BudgetReason",
    "BudgetScope",
    "BudgetValue",
    "ExecutionBudgetExceeded",
    "REQUEST_BUDGET_METADATA_KEY",
    "RequestBudgetWindow",
]
