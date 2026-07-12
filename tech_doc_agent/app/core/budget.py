from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.model_pricing import CostEstimate, ModelPriceTable


@dataclass(frozen=True, slots=True)
class LlmUsage:
    calls: int
    provider: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None

    def __post_init__(self) -> None:
        if isinstance(self.calls, bool) or not isinstance(self.calls, int) or self.calls < 1:
            raise ValueError("calls must be greater than or equal to 1")
        if not self.provider or self.provider != self.provider.strip():
            raise ValueError("provider must be a non-empty trimmed string")
        if self.model is not None and (
            not self.model or self.model != self.model.strip()
        ):
            raise ValueError("model must be None or a non-empty trimmed string")
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("total_tokens", self.total_tokens),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative integer or None")

    @classmethod
    def from_message(
        cls,
        message: Any,
        *,
        default_provider: str,
        calls: int = 1,
    ) -> LlmUsage:
        usage = getattr(message, "usage_metadata", None)
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = _optional_nonnegative_int(usage.get("input_tokens"))
        output_tokens = _optional_nonnegative_int(usage.get("output_tokens"))
        total_tokens = _optional_nonnegative_int(usage.get("total_tokens"))
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        response_metadata = getattr(message, "response_metadata", None)
        response_metadata = response_metadata if isinstance(response_metadata, dict) else {}
        provider = _optional_string(
            response_metadata.get("model_provider") or response_metadata.get("provider")
        ) or default_provider
        model = _optional_string(
            response_metadata.get("model_name") or response_metadata.get("model")
        )
        return cls(
            calls=calls,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    schema_version: int
    workflow_started_at: str
    llm_calls: int = 0
    tool_calls: int = 0
    reported_input_tokens: int = 0
    reported_output_tokens: int = 0
    reported_total_tokens: int = 0
    unreported_input_token_calls: int = 0
    unreported_output_token_calls: int = 0
    unreported_total_token_calls: int = 0
    priced_cost_usd: Decimal = Decimal("0")
    unpriced_llm_calls: int = 0

    @classmethod
    def new(cls, *, now: datetime | None = None) -> BudgetUsage:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return cls(
            schema_version=1,
            workflow_started_at=now.astimezone(UTC).isoformat(),
        )

    @classmethod
    def from_state(cls, payload: Any) -> BudgetUsage:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise _invalid_budget_usage()
        workflow_started_at = payload.get("workflow_started_at")
        if not isinstance(workflow_started_at, str) or not workflow_started_at:
            raise _invalid_budget_usage()
        try:
            parsed_started_at = datetime.fromisoformat(
                workflow_started_at.replace("Z", "+00:00")
            )
        except ValueError:
            raise _invalid_budget_usage() from None
        if parsed_started_at.tzinfo is None:
            raise _invalid_budget_usage()

        integer_fields = {
            field_name: _required_nonnegative_int(payload.get(field_name), field_name)
            for field_name in (
                "llm_calls",
                "tool_calls",
                "reported_input_tokens",
                "reported_output_tokens",
                "reported_total_tokens",
                "unreported_input_token_calls",
                "unreported_output_token_calls",
                "unreported_total_token_calls",
                "unpriced_llm_calls",
            )
        }
        priced_cost_usd = _required_nonnegative_decimal(payload.get("priced_cost_usd"))
        return cls(
            schema_version=1,
            workflow_started_at=workflow_started_at,
            priced_cost_usd=priced_cost_usd,
            **integer_fields,
        )

    @property
    def input_tokens(self) -> int | None:
        return (
            None
            if self.unreported_input_token_calls
            else self.reported_input_tokens
        )

    @property
    def output_tokens(self) -> int | None:
        return (
            None
            if self.unreported_output_token_calls
            else self.reported_output_tokens
        )

    @property
    def total_tokens(self) -> int | None:
        return (
            None
            if self.unreported_total_token_calls
            else self.reported_total_tokens
        )

    @property
    def estimated_cost_usd(self) -> Decimal | None:
        return None if self.unpriced_llm_calls else self.priced_cost_usd

    def record_llm(
        self,
        usage: LlmUsage,
        price_table: ModelPriceTable,
    ) -> tuple[BudgetUsage, CostEstimate]:
        estimate = price_table.estimate(
            provider=usage.provider,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        return (
            replace(
                self,
                llm_calls=self.llm_calls + usage.calls,
                reported_input_tokens=(
                    self.reported_input_tokens + (usage.input_tokens or 0)
                ),
                reported_output_tokens=(
                    self.reported_output_tokens + (usage.output_tokens or 0)
                ),
                reported_total_tokens=(
                    self.reported_total_tokens + (usage.total_tokens or 0)
                ),
                unreported_input_token_calls=(
                    self.unreported_input_token_calls
                    + (usage.calls if usage.input_tokens is None else 0)
                ),
                unreported_output_token_calls=(
                    self.unreported_output_token_calls
                    + (usage.calls if usage.output_tokens is None else 0)
                ),
                unreported_total_token_calls=(
                    self.unreported_total_token_calls
                    + (usage.calls if usage.total_tokens is None else 0)
                ),
                priced_cost_usd=(
                    self.priced_cost_usd + (estimate.estimated_cost_usd or Decimal("0"))
                ),
                unpriced_llm_calls=(
                    self.unpriced_llm_calls + (0 if estimate.is_priced else usage.calls)
                ),
            ),
            estimate,
        )

    def record_tools(self, calls: int = 1) -> BudgetUsage:
        if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
            raise ValueError("calls must be non-negative")
        return replace(self, tool_calls=self.tool_calls + calls)

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workflow_started_at": self.workflow_started_at,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "reported_input_tokens": self.reported_input_tokens,
            "reported_output_tokens": self.reported_output_tokens,
            "reported_total_tokens": self.reported_total_tokens,
            "unreported_input_token_calls": self.unreported_input_token_calls,
            "unreported_output_token_calls": self.unreported_output_token_calls,
            "unreported_total_token_calls": self.unreported_total_token_calls,
            "estimated_cost_usd": (
                float(self.estimated_cost_usd)
                if self.estimated_cost_usd is not None
                else None
            ),
            "priced_cost_usd": str(self.priced_cost_usd),
            "unpriced_llm_calls": self.unpriced_llm_calls,
        }


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _required_nonnegative_int(value: Any, field_name: str) -> int:
    parsed = _optional_nonnegative_int(value)
    if parsed is None:
        raise _invalid_budget_usage(field_name)
    return parsed


def _required_nonnegative_decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _invalid_budget_usage("priced_cost_usd") from None
    if not parsed.is_finite() or parsed < 0:
        raise _invalid_budget_usage("priced_cost_usd")
    return parsed


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _invalid_budget_usage(field_name: str | None = None) -> ValidationError:
    return ValidationError(
        "The persisted workflow budget usage is invalid.",
        code="budget_usage_invalid",
        dependency="workflow_state",
        cause_type=field_name or "BudgetUsageValidation",
    )


__all__ = ["BudgetUsage", "LlmUsage"]
