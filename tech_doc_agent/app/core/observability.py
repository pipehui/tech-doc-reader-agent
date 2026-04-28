from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import json
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4


_LOGGER = logging.getLogger("tech_doc_agent.observability")
_LOGGER.setLevel(logging.INFO)
if not _LOGGER.handlers:
    _HANDLER = logging.StreamHandler()
    _HANDLER.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(_HANDLER)
_LOGGER.propagate = False

_TRACE_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "trace_context",
    default={},
)


def new_trace_id() -> str:
    return f"trace_{uuid4().hex}"


def get_trace_context() -> dict[str, Any]:
    return dict(_TRACE_CONTEXT.get())


@contextmanager
def trace_context(**fields: Any) -> Iterator[dict[str, Any]]:
    previous = get_trace_context()
    current = {**previous, **{key: value for key, value in fields.items() if value is not None}}
    token = _TRACE_CONTEXT.set(current)

    try:
        yield current
    finally:
        _TRACE_CONTEXT.reset(token)


def _json_default(value: Any) -> str:
    return str(value)


def _elapsed_ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 2)


def log_event(event: str, **fields: Any) -> None:
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **get_trace_context(),
        **fields,
    }

    _LOGGER.info(json.dumps(payload, ensure_ascii=False, default=_json_default))


@contextmanager
def timed_node(name: str, **fields: Any) -> Iterator[None]:
    start = perf_counter()
    event_fields = {"node": name, **fields}
    log_event("node.started", **event_fields)

    try:
        yield
    except Exception as exc:
        log_event(
            "node.error",
            **event_fields,
            elapsed_ms=_elapsed_ms(start),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise

    log_event("node.finished", elapsed_ms=_elapsed_ms(start), **event_fields)
