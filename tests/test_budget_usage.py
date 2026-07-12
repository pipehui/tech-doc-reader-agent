from datetime import UTC, datetime
from decimal import Decimal

from langchain_core.messages import AIMessage
import pytest

from tech_doc_agent.app.core.budget import BudgetUsage, LlmUsage
from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tests.test_model_pricing import PRICE_TABLE_PAYLOAD


def test_llm_usage_reads_message_metadata_instead_of_sse_chunk_count():
    message = AIMessage(
        content="answer",
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 500,
            "total_tokens": 1500,
        },
        response_metadata={
            "model_provider": "provider-a",
            "model_name": "model-a",
        },
    )

    usage = LlmUsage.from_message(message, default_provider="fallback-provider")

    assert usage == LlmUsage(
        calls=1,
        provider="provider-a",
        model="model-a",
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
    )


def test_llm_usage_keeps_missing_provider_usage_unknown():
    usage = LlmUsage.from_message(
        AIMessage(content="answer"),
        default_provider="openai_compatible",
    )

    assert usage.provider == "openai_compatible"
    assert usage.model is None
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"calls": 0},
        {"provider": ""},
        {"model": " model "},
        {"input_tokens": -1},
    ],
)
def test_llm_usage_rejects_invalid_direct_construction(kwargs):
    values = {
        "calls": 1,
        "provider": "provider",
        "model": "model",
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        LlmUsage(**values)


def test_budget_usage_accumulates_priced_llm_and_tool_calls_and_round_trips():
    usage = BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC))
    llm = LlmUsage(
        calls=1,
        provider="openai_compatible",
        model="test-model",
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
    )

    usage, estimate = usage.record_llm(
        llm,
        ModelPriceTable.from_payload(PRICE_TABLE_PAYLOAD),
    )
    usage = usage.record_tools(2)
    payload = usage.to_state()

    assert estimate.estimated_cost_usd == Decimal("0.006")
    assert payload["llm_calls"] == 1
    assert payload["tool_calls"] == 2
    assert payload["input_tokens"] == 1000
    assert payload["output_tokens"] == 500
    assert payload["total_tokens"] == 1500
    assert payload["estimated_cost_usd"] == 0.006
    assert BudgetUsage.from_state(payload) == usage


def test_unreported_tokens_and_unknown_price_remain_unknown_after_accumulation():
    usage = BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC))
    missing = LlmUsage(
        calls=2,
        provider="openai_compatible",
        model=None,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )

    usage, estimate = usage.record_llm(missing, ModelPriceTable.empty())
    payload = usage.to_state()

    assert estimate.reason == "token_usage_unreported"
    assert payload["llm_calls"] == 2
    assert payload["input_tokens"] is None
    assert payload["output_tokens"] is None
    assert payload["total_tokens"] is None
    assert payload["estimated_cost_usd"] is None
    assert payload["reported_total_tokens"] == 0
    assert payload["unreported_total_token_calls"] == 2
    assert payload["unpriced_llm_calls"] == 2


def test_one_unpriced_call_keeps_aggregate_cost_unknown_instead_of_filling_zero():
    table = ModelPriceTable.from_payload(PRICE_TABLE_PAYLOAD)
    usage = BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC))
    usage, _ = usage.record_llm(
        LlmUsage(1, "openai_compatible", "test-model", 1000, 500, 1500),
        table,
    )
    usage, _ = usage.record_llm(
        LlmUsage(1, "openai_compatible", "unknown-model", 20, 10, 30),
        table,
    )

    assert usage.priced_cost_usd == Decimal("0.006")
    assert usage.estimated_cost_usd is None
    assert usage.to_state()["estimated_cost_usd"] is None


@pytest.mark.parametrize(
    "field_value",
    [
        None,
        {},
        {"schema_version": 2},
        {
            **BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC)).to_state(),
            "llm_calls": -1,
        },
        {
            **BudgetUsage.new(now=datetime(2026, 7, 12, tzinfo=UTC)).to_state(),
            "priced_cost_usd": "nan",
        },
    ],
)
def test_budget_usage_rejects_corrupt_checkpoint_payload(field_value):
    with pytest.raises(ValidationError) as exc_info:
        BudgetUsage.from_state(field_value)

    assert exc_info.value.code == "budget_usage_invalid"
    assert exc_info.value.dependency == "workflow_state"
