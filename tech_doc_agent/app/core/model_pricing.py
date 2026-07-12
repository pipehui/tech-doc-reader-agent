from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from tech_doc_agent.app.core.errors import ValidationError


@dataclass(frozen=True, slots=True)
class ModelPrice:
    provider: str
    model: str
    version: str
    effective_at: str
    input_usd_per_million_tokens: Decimal
    output_usd_per_million_tokens: Decimal

    @classmethod
    def from_payload(cls, payload: Any) -> ModelPrice:
        if not isinstance(payload, dict):
            raise _invalid_price_table("Price entries must be objects.")
        provider = _required_string(payload.get("provider"), "provider")
        model = _required_string(payload.get("model"), "model")
        version = _required_string(payload.get("version"), "version")
        effective_at = _required_timestamp(payload.get("effective_at"), "effective_at")
        return cls(
            provider=provider,
            model=model,
            version=version,
            effective_at=effective_at,
            input_usd_per_million_tokens=_nonnegative_decimal(
                payload.get("input_usd_per_million_tokens"),
                "input_usd_per_million_tokens",
            ),
            output_usd_per_million_tokens=_nonnegative_decimal(
                payload.get("output_usd_per_million_tokens"),
                "output_usd_per_million_tokens",
            ),
        )


@dataclass(frozen=True, slots=True)
class CostEstimate:
    provider: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: Decimal | None
    price_table_version: str | None
    price_version: str | None
    price_effective_at: str | None
    reason: str

    @property
    def is_priced(self) -> bool:
        return self.estimated_cost_usd is not None

    def to_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": (
                float(self.estimated_cost_usd)
                if self.estimated_cost_usd is not None
                else None
            ),
            "price_table_version": self.price_table_version,
            "price_version": self.price_version,
            "price_effective_at": self.price_effective_at,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ModelPriceTable:
    schema_version: int
    table_version: str
    effective_at: str | None
    entries: tuple[ModelPrice, ...]

    @classmethod
    def empty(cls) -> ModelPriceTable:
        return cls(
            schema_version=1,
            table_version="unconfigured",
            effective_at=None,
            entries=(),
        )

    @classmethod
    def from_payload(cls, payload: Any) -> ModelPriceTable:
        if not isinstance(payload, dict):
            raise _invalid_price_table("The model price table must be an object.")
        schema_version = payload.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 1
        ):
            raise _invalid_price_table("Unsupported model price table schema version.")
        table_version = _required_string(payload.get("table_version"), "table_version")
        effective_at = _required_timestamp(payload.get("effective_at"), "effective_at")
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise _invalid_price_table("The model price table entries must be a list.")
        entries = tuple(ModelPrice.from_payload(entry) for entry in raw_entries)
        keys = [(entry.provider, entry.model) for entry in entries]
        if len(keys) != len(set(keys)):
            raise _invalid_price_table("The model price table contains duplicate provider/model entries.")
        return cls(
            schema_version=1,
            table_version=table_version,
            effective_at=effective_at,
            entries=entries,
        )

    def estimate(
        self,
        *,
        provider: str,
        model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> CostEstimate:
        _validate_optional_tokens(input_tokens, "input_tokens")
        _validate_optional_tokens(output_tokens, "output_tokens")
        if input_tokens is None or output_tokens is None:
            return CostEstimate(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=None,
                price_table_version=None,
                price_version=None,
                price_effective_at=None,
                reason="token_usage_unreported",
            )
        if model is None:
            return CostEstimate(
                provider=provider,
                model=None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=None,
                price_table_version=None,
                price_version=None,
                price_effective_at=None,
                reason="model_identity_unreported",
            )
        entry = next(
            (
                candidate
                for candidate in self.entries
                if candidate.provider == provider and candidate.model == model
            ),
            None,
        )
        if entry is None:
            return CostEstimate(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=None,
                price_table_version=self.table_version,
                price_version=None,
                price_effective_at=None,
                reason="price_not_configured",
            )

        cost = (
            Decimal(input_tokens) * entry.input_usd_per_million_tokens
            + Decimal(output_tokens) * entry.output_usd_per_million_tokens
        ) / Decimal(1_000_000)
        return CostEstimate(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            price_table_version=self.table_version,
            price_version=entry.version,
            price_effective_at=entry.effective_at,
            reason="priced",
        )


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _invalid_price_table(f"Price field '{field_name}' must be a non-empty trimmed string.")
    return value


def _required_timestamp(value: Any, field_name: str) -> str:
    raw = _required_string(value, field_name)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise _invalid_price_table(f"Price field '{field_name}' must be an ISO timestamp.") from None
    if parsed.tzinfo is None:
        raise _invalid_price_table(f"Price field '{field_name}' must include a timezone.")
    return raw


def _validate_optional_tokens(value: int | None, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer or None")


def _nonnegative_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise _invalid_price_table(f"Price field '{field_name}' must be non-negative.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _invalid_price_table(f"Price field '{field_name}' must be non-negative.") from None
    if not parsed.is_finite() or parsed < 0:
        raise _invalid_price_table(f"Price field '{field_name}' must be non-negative.")
    return parsed


def _invalid_price_table(message: str) -> ValidationError:
    return ValidationError(
        message,
        code="model_price_table_invalid",
        dependency="model_pricing",
        cause_type="PriceTableValidation",
    )


__all__ = ["CostEstimate", "ModelPrice", "ModelPriceTable"]
