from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings

try:
    Langfuse: Any = getattr(import_module("langfuse"), "Langfuse")
    CallbackHandler: Any = getattr(import_module("langfuse.langchain"), "CallbackHandler")
except ImportError:  # pragma: no cover - exercised when optional package is absent
    Langfuse = None
    CallbackHandler = None


_CLIENT: Any | None = None


@dataclass(frozen=True)
class LangfuseTrace:
    callback: Any
    trace_id: str
    trace_url: str | None = None


def _base_url(settings: Settings) -> str | None:
    return settings.LANGFUSE_BASE_URL or settings.LANGFUSE_HOST or None


def _configured(settings: Settings) -> bool:
    return (
        settings.LANGFUSE_ENABLED
        and bool(settings.LANGFUSE_PUBLIC_KEY)
        and bool(settings.LANGFUSE_SECRET_KEY)
    )


def _ensure_client(settings: Settings):
    global _CLIENT

    if _CLIENT is not None:
        return _CLIENT

    if Langfuse is None:
        log_event("langfuse.unavailable", reason="package_not_installed")
        return None

    _CLIENT = Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        base_url=_base_url(settings),
        tracing_enabled=True,
        environment=settings.LANGFUSE_ENVIRONMENT or None,
        release=settings.LANGFUSE_RELEASE or None,
    )
    log_event(
        "langfuse.client.initialized",
        base_url=_base_url(settings),
        environment=settings.LANGFUSE_ENVIRONMENT or None,
        release=settings.LANGFUSE_RELEASE or None,
    )
    return _CLIENT


def build_langfuse_trace(settings: Settings, external_trace_id: str) -> LangfuseTrace | None:
    if not _configured(settings):
        return None

    if Langfuse is None or CallbackHandler is None:
        log_event("langfuse.unavailable", reason="package_not_installed")
        return None

    _ensure_client(settings)
    langfuse_trace_id = Langfuse.create_trace_id(seed=external_trace_id)
    callback = CallbackHandler(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        trace_context={"trace_id": langfuse_trace_id},
    )
    trace_url = get_langfuse_trace_url(settings, langfuse_trace_id)

    log_event(
        "langfuse.trace.prepared",
        langfuse_trace_id=langfuse_trace_id,
        langfuse_trace_url=trace_url,
    )

    return LangfuseTrace(
        callback=callback,
        trace_id=langfuse_trace_id,
        trace_url=trace_url,
    )


def langfuse_metadata(
    session_id: str,
    operation: str,
    external_trace_id: str | None,
    langfuse_trace: LangfuseTrace | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "langfuse_session_id": session_id,
        "langfuse_tags": ["tech-doc-agent", operation],
    }

    if external_trace_id:
        metadata["trace_id"] = external_trace_id
        metadata["external_trace_id"] = external_trace_id

    if langfuse_trace is not None:
        metadata["langfuse_trace_id"] = langfuse_trace.trace_id
        if langfuse_trace.trace_url:
            metadata["langfuse_trace_url"] = langfuse_trace.trace_url

    return metadata


def get_langfuse_trace_url(settings: Settings, trace_id: str) -> str | None:
    if not trace_id:
        return None

    client = _ensure_client(settings)
    if client is None:
        return None

    try:
        return client.get_trace_url(trace_id=trace_id)
    except Exception as exc:  # pragma: no cover - SDK should not fail normal flow
        log_event(
            "langfuse.trace_url.error",
            langfuse_trace_id=trace_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


def flush_langfuse(settings: Settings) -> None:
    if not settings.LANGFUSE_ENABLED or _CLIENT is None:
        return

    try:
        _CLIENT.flush()
    except Exception as exc:  # pragma: no cover - SDK documents this as non-throwing
        log_event(
            "langfuse.flush.error",
            error_type=type(exc).__name__,
            error=str(exc),
        )


def shutdown_langfuse(settings: Settings) -> None:
    if not settings.LANGFUSE_ENABLED or _CLIENT is None:
        return

    try:
        _CLIENT.shutdown()
    except Exception as exc:  # pragma: no cover - shutdown is best effort
        log_event(
            "langfuse.shutdown.error",
            error_type=type(exc).__name__,
            error=str(exc),
        )
