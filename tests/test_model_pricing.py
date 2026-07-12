from decimal import Decimal

import pytest

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.model_pricing import ModelPriceTable
from tech_doc_agent.app.infrastructure.model_price_table import load_model_price_table


PRICE_TABLE_PAYLOAD = {
    "schema_version": 1,
    "table_version": "test-prices-v1",
    "effective_at": "2026-07-12T00:00:00+00:00",
    "entries": [
        {
            "provider": "openai_compatible",
            "model": "test-model",
            "version": "2026-07-12",
            "effective_at": "2026-07-12T00:00:00+00:00",
            "input_usd_per_million_tokens": "2.0",
            "output_usd_per_million_tokens": "8.0",
        }
    ],
}


def test_price_table_estimates_with_provider_model_version_and_effective_date():
    table = ModelPriceTable.from_payload(PRICE_TABLE_PAYLOAD)

    estimate = table.estimate(
        provider="openai_compatible",
        model="test-model",
        input_tokens=1000,
        output_tokens=500,
    )

    assert estimate.estimated_cost_usd == Decimal("0.006")
    assert estimate.price_table_version == "test-prices-v1"
    assert estimate.price_version == "2026-07-12"
    assert estimate.price_effective_at == "2026-07-12T00:00:00+00:00"
    assert estimate.reason == "priced"
    assert estimate.to_payload()["estimated_cost_usd"] == 0.006


def test_unknown_model_or_unreported_tokens_produces_unknown_cost_not_zero():
    table = ModelPriceTable.from_payload(PRICE_TABLE_PAYLOAD)

    unknown_model = table.estimate(
        provider="openai_compatible",
        model="unknown-model",
        input_tokens=10,
        output_tokens=5,
    )
    unreported_usage = table.estimate(
        provider="openai_compatible",
        model="test-model",
        input_tokens=None,
        output_tokens=None,
    )

    assert unknown_model.estimated_cost_usd is None
    assert unknown_model.reason == "price_not_configured"
    assert unknown_model.to_payload()["estimated_cost_usd"] is None
    assert unreported_usage.estimated_cost_usd is None
    assert unreported_usage.reason == "token_usage_unreported"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(schema_version=2),
        lambda payload: payload.update(table_version=""),
        lambda payload: payload.update(effective_at="2026-07-12"),
        lambda payload: payload.update(entries="not-a-list"),
        lambda payload: payload["entries"][0].update(input_usd_per_million_tokens="-1"),
        lambda payload: payload["entries"].append(dict(payload["entries"][0])),
    ],
)
def test_price_table_rejects_invalid_or_ambiguous_configuration(mutate):
    payload = {
        **PRICE_TABLE_PAYLOAD,
        "entries": [dict(PRICE_TABLE_PAYLOAD["entries"][0])],
    }
    mutate(payload)

    with pytest.raises(ValidationError) as exc_info:
        ModelPriceTable.from_payload(payload)

    assert exc_info.value.code == "model_price_table_invalid"
    assert exc_info.value.dependency == "model_pricing"


def test_price_table_loader_defaults_to_empty_and_maps_file_errors_safely(tmp_path):
    assert load_model_price_table("").entries == ()
    broken_path = tmp_path / "private-prices.json"
    broken_path.write_text("{private", encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        load_model_price_table(broken_path)

    assert exc_info.value.code == "model_price_table_invalid"
    assert "private-prices" not in str(exc_info.value)
    assert "{private" not in str(exc_info.value)
