from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from tech_doc_agent.app.core.redaction import RedactionPolicy, telemetry_redaction_policy
from tech_doc_agent.app.core.settings import get_settings


def artifact_redaction_policy() -> RedactionPolicy:
    settings = get_settings()
    return telemetry_redaction_policy(
        settings.TELEMETRY_PSEUDONYM_KEY.get_secret_value()
    )


def redact_artifact_rows(
    rows: list[dict[str, Any]],
    *,
    policy: RedactionPolicy | None = None,
) -> list[dict[str, Any]]:
    safe_rows = (policy or artifact_redaction_policy()).redact(rows)
    return cast(list[dict[str, Any]], safe_rows)


def safe_artifact_text(value: Any, *, policy: RedactionPolicy | None = None) -> str:
    return (policy or artifact_redaction_policy()).redact_text(str(value))


def write_jsonl(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    policy: RedactionPolicy | None = None,
) -> None:
    safe_rows = redact_artifact_rows(rows, policy=policy)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in safe_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


__all__ = [
    "artifact_redaction_policy",
    "redact_artifact_rows",
    "safe_artifact_text",
    "write_jsonl",
]
