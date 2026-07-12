from __future__ import annotations

import json
from pathlib import Path

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.model_pricing import ModelPriceTable


def load_model_price_table(path: str | Path | None) -> ModelPriceTable:
    if path is None or not str(path).strip():
        return ModelPriceTable.empty()

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            "The configured model price table could not be loaded.",
            code="model_price_table_invalid",
            dependency="model_pricing",
            cause=exc,
        ) from exc
    return ModelPriceTable.from_payload(payload)


__all__ = ["load_model_price_table"]
