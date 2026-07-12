from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import BaseMessage, message_to_dict

from tech_doc_agent.app.core.context_metrics import ContextScope, ContextSnapshot


def measure_context(
    *,
    state: Mapping[str, Any],
    prompt_state: Mapping[str, Any],
    agent: str,
    scope: ContextScope,
) -> ContextSnapshot:
    checkpoint_messages = list(state.get("messages", []))
    prompt_messages = list(prompt_state.get("messages", []))
    return ContextSnapshot(
        agent=agent,
        scope=scope,
        checkpoint_message_count=len(checkpoint_messages),
        checkpoint_serialized_bytes=estimate_serialized_bytes(state),
        prompt_message_count=len(prompt_messages),
        prompt_serialized_bytes=estimate_serialized_bytes(prompt_messages),
    )


def estimate_serialized_bytes(value: Any) -> int | None:
    """Estimate UTF-8 JSON bytes without emitting the serialized content."""

    try:
        encoded = _canonical_json_bytes(value)
    except Exception:
        return None
    return len(encoded)


def serialized_sha256(value: Any) -> str | None:
    """Hash the same content-safe canonical representation used for sizing."""

    try:
        return sha256(_canonical_json_bytes(value)).hexdigest()
    except Exception:
        return None


def _canonical_json_bytes(value: Any) -> bytes:
    normalized = _jsonable(value, seen=set(), depth=0)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _jsonable(value: Any, *, seen: set[int], depth: int) -> Any:
    if depth > 64:
        return {"type": type(value).__name__, "truncated": True}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseMessage):
        return message_to_dict(value)

    object_id = id(value)
    if object_id in seen:
        return {"type": type(value).__name__, "cycle": True}
    if isinstance(value, dict):
        seen.add(object_id)
        try:
            return {
                str(key): _jsonable(item, seen=seen, depth=depth + 1)
                for key, item in value.items()
            }
        finally:
            seen.remove(object_id)
    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(object_id)
        try:
            return [
                _jsonable(item, seen=seen, depth=depth + 1)
                for item in value
            ]
        finally:
            seen.remove(object_id)
    return {"type": f"{type(value).__module__}.{type(value).__name__}"}


__all__ = ["estimate_serialized_bytes", "measure_context", "serialized_sha256"]
