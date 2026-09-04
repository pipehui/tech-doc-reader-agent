from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
from time import perf_counter
import traceback
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from tech_doc_agent.app.core.settings import Settings


TRACE_SCHEMA_VERSION = 1
_ACTIVE_SUFFIX = ".active.jsonl"
_FINAL_SUFFIX = ".jsonl"
_LOGGER = logging.getLogger("tech_doc_agent.local_tracing")
_ACTIVE_TRACE: ContextVar[LocalTrace | None] = ContextVar(
    "active_local_trace",
    default=None,
)
_INITIALIZED_DIRECTORIES: set[Path] = set()
_INITIALIZATION_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _warning(event: str, exc: BaseException, *, path: Path | None = None) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "error_type": type(exc).__name__,
    }
    if path is not None:
        payload["path"] = str(path)
    _LOGGER.warning(json.dumps(payload, ensure_ascii=False))


def _trace_directory(settings: Settings) -> Path:
    return Path(settings.DATA_PATH) / "traces"


def _safe_trace_component(trace_id: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "_"
        for character in trace_id
    )
    digest = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:80]}__{digest}"


def _trace_paths(directory: Path, trace_id: str, started_at: datetime) -> tuple[Path, Path]:
    timestamp = started_at.strftime("%Y%m%dT%H%M%S.%fZ")
    stem = f"{timestamp}__{_safe_trace_component(trace_id)}"
    return (
        directory / f"{stem}{_ACTIVE_SUFFIX}",
        directory / f"{stem}{_FINAL_SUFFIX}",
    )


def _is_active_path(path: Path) -> bool:
    return path.name.endswith(_ACTIVE_SUFFIX)


def _completed_paths(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.glob(f"*{_FINAL_SUFFIX}")
        if path.is_file() and not _is_active_path(path)
    ]


def _prune_completed(directory: Path, retention_count: int) -> None:
    try:
        completed = sorted(
            _completed_paths(directory),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
        for path in completed[retention_count:]:
            try:
                path.unlink()
            except OSError as exc:
                _warning("local_trace.cleanup.error", exc, path=path)
    except OSError as exc:
        _warning("local_trace.cleanup.error", exc, path=directory)


def _last_sequence(path: Path) -> int:
    last_sequence = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except (TypeError, ValueError):
                    continue
                sequence = value.get("seq") if isinstance(value, dict) else None
                if isinstance(sequence, int):
                    last_sequence = max(last_sequence, sequence)
    except OSError:
        return 0
    return last_sequence


def _recovered_final_path(active_path: Path) -> Path:
    final_path = active_path.with_name(
        active_path.name.removesuffix(_ACTIVE_SUFFIX) + _FINAL_SUFFIX
    )
    if not final_path.exists():
        return final_path
    recovered_at = _utc_now().strftime("%Y%m%dT%H%M%S.%fZ")
    return final_path.with_name(f"{final_path.stem}__recovered_{recovered_at}{_FINAL_SUFFIX}")


def _recover_active_trace(active_path: Path) -> None:
    context: dict[str, Any] = {}
    try:
        with active_path.open("r", encoding="utf-8") as handle:
            first_row = json.loads(handle.readline())
        if isinstance(first_row, dict):
            for key in ("trace_id", "session_id", "user_id", "namespace", "operation"):
                if key in first_row:
                    context[key] = first_row[key]
    except (OSError, TypeError, ValueError):
        pass

    row = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "seq": _last_sequence(active_path) + 1,
        "timestamp": _utc_now().isoformat(),
        **context,
        "record_type": "trace.end",
        "status": "abandoned",
        "payload": {"reason": "process_restart"},
    }
    try:
        encoded = (json.dumps(row, ensure_ascii=False, default=repr) + "\n").encode("utf-8")
        with active_path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(active_path, _recovered_final_path(active_path))
    except OSError as exc:
        _warning("local_trace.recovery.error", exc, path=active_path)


def initialize_local_tracing(settings: Settings) -> None:
    if not settings.LOCAL_TRACE_ENABLED:
        return

    directory = _trace_directory(settings).resolve()
    with _INITIALIZATION_LOCK:
        if directory in _INITIALIZED_DIRECTORIES:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for active_path in sorted(directory.glob(f"*{_ACTIVE_SUFFIX}")):
                if active_path.is_file():
                    _recover_active_trace(active_path)
            _prune_completed(directory, settings.LOCAL_TRACE_RETENTION_COUNT)
        except OSError as exc:
            _warning("local_trace.initialization.error", exc, path=directory)
            return
        _INITIALIZED_DIRECTORIES.add(directory)


def _json_value(value: Any, active: set[int] | None = None) -> Any:
    active = active if active is not None else set()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Path, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }

    identity = id(value)
    if identity in active:
        return {"type": type(value).__name__, "value": "[CYCLE]"}

    if isinstance(value, Mapping):
        active.add(identity)
        try:
            return {str(key): _json_value(item, active) for key, item in value.items()}
        finally:
            active.remove(identity)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        active.add(identity)
        try:
            return [_json_value(item, active) for item in value]
        finally:
            active.remove(identity)
    if isinstance(value, (set, frozenset)):
        active.add(identity)
        try:
            return [_json_value(item, active) for item in sorted(value, key=repr)]
        finally:
            active.remove(identity)

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        active.add(identity)
        try:
            return _json_value(model_dump(mode="json"), active)
        except Exception:
            try:
                return _json_value(model_dump(), active)
            except Exception:
                pass
        finally:
            active.remove(identity)

    if is_dataclass(value) and not isinstance(value, type):
        active.add(identity)
        try:
            return {
                field.name: _json_value(getattr(value, field.name), active)
                for field in fields(value)
            }
        finally:
            active.remove(identity)

    try:
        return {"type": type(value).__name__, "repr": repr(value)}
    except Exception:
        return {"type": type(value).__name__, "repr": "[UNAVAILABLE]"}


def _exception_payload(exc: BaseException) -> dict[str, Any]:
    try:
        message = str(exc)
    except Exception:
        message = "[UNAVAILABLE]"
    try:
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        stack = "[UNAVAILABLE]"
    return {
        "type": type(exc).__name__,
        "message": message,
        "traceback": stack,
    }


@dataclass(slots=True)
class LocalTrace:
    trace_id: str
    session_id: str
    user_id: str
    namespace: str
    operation: str
    active_path: Path
    final_path: Path
    retention_count: int
    max_payload_bytes: int
    capture_content: bool
    started_at: datetime
    started_monotonic: float
    _sequence: int = 0
    _payload_bytes: int = 0
    _truncated: bool = False
    _finished: bool = False
    _lock: threading.RLock | None = None

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    @property
    def finished(self) -> bool:
        return self._finished

    def _common_row(self, record_type: str) -> dict[str, Any]:
        self._sequence += 1
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "seq": self._sequence,
            "timestamp": _utc_now().isoformat(),
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "namespace": self.namespace,
            "operation": self.operation,
            "record_type": record_type,
        }

    def _write_row(self, row: Mapping[str, Any], *, fsync: bool = False) -> bool:
        try:
            encoded = (json.dumps(row, ensure_ascii=False, default=repr) + "\n").encode("utf-8")
            with self.active_path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                if fsync:
                    os.fsync(handle.fileno())
            return True
        except (OSError, TypeError, ValueError) as exc:
            _warning("local_trace.write.error", exc, path=self.active_path)
            return False

    def _payload_for_write(self, payload: Any) -> Any:
        if not self.capture_content:
            return {"content_omitted": True, "reason": "capture_disabled"}

        safe_payload = _json_value(payload)
        encoded_size = len(json.dumps(safe_payload, ensure_ascii=False, default=repr).encode("utf-8"))
        if self._payload_bytes + encoded_size <= self.max_payload_bytes:
            self._payload_bytes += encoded_size
            return safe_payload

        if not self._truncated:
            self._truncated = True
            truncated_row = self._common_row("trace.truncated")
            truncated_row["status"] = "truncated"
            truncated_row["payload"] = {
                "reason": "trace_size_limit",
                "max_payload_bytes": self.max_payload_bytes,
                "payload_bytes_written": self._payload_bytes,
            }
            self._write_row(truncated_row)
        return {
            "content_omitted": True,
            "reason": "trace_size_limit",
            "original_bytes": encoded_size,
        }

    def record(
        self,
        record_type: str,
        *,
        name: str | None = None,
        status: str | None = None,
        span_kind: str | None = None,
        run_id: UUID | str | None = None,
        parent_run_id: UUID | str | None = None,
        elapsed_ms: float | None = None,
        payload: Any = None,
    ) -> None:
        if self._lock is None:
            return
        try:
            with self._lock:
                if self._finished:
                    return
                prepared_payload = self._payload_for_write(payload) if payload is not None else None
                row = self._common_row(record_type)
                optional = {
                    "name": name,
                    "status": status,
                    "span_kind": span_kind,
                    "run_id": str(run_id) if run_id is not None else None,
                    "parent_run_id": str(parent_run_id) if parent_run_id is not None else None,
                    "elapsed_ms": elapsed_ms,
                }
                row.update({key: value for key, value in optional.items() if value is not None})
                if prepared_payload is not None:
                    row["payload"] = prepared_payload
                self._write_row(row)
        except Exception as exc:
            _warning("local_trace.record.error", exc, path=self.active_path)

    def record_exception(
        self,
        exc: BaseException,
        *,
        name: str,
        run_id: UUID | str | None = None,
        parent_run_id: UUID | str | None = None,
        span_kind: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        self.record(
            "span.error" if span_kind is not None else "trace.error",
            name=name,
            status="error",
            span_kind=span_kind,
            run_id=run_id,
            parent_run_id=parent_run_id,
            elapsed_ms=elapsed_ms,
            payload={"error": _exception_payload(exc)},
        )

    def finish(self, status: str, *, payload: Any = None) -> None:
        if self._lock is None:
            return
        try:
            with self._lock:
                if self._finished:
                    return
                prepared_payload = self._payload_for_write(payload) if payload is not None else None
                row = self._common_row("trace.end")
                row["status"] = status
                row["elapsed_ms"] = round((perf_counter() - self.started_monotonic) * 1000, 2)
                row["truncated"] = self._truncated
                if prepared_payload is not None:
                    row["payload"] = prepared_payload
                written = self._write_row(row, fsync=True)
                self._finished = True
                if not written:
                    return
                try:
                    os.replace(self.active_path, self.final_path)
                except OSError as exc:
                    _warning("local_trace.finalize.error", exc, path=self.active_path)
                    return
                _prune_completed(self.final_path.parent, self.retention_count)
        except Exception as exc:
            _warning("local_trace.finish.error", exc, path=self.active_path)


def begin_local_trace(
    settings: Settings | None,
    *,
    trace_id: str,
    session_id: str,
    user_id: str,
    namespace: str,
    operation: str,
    request_payload: Any,
) -> LocalTrace | None:
    if settings is None or not settings.LOCAL_TRACE_ENABLED:
        return None

    initialize_local_tracing(settings)
    directory = _trace_directory(settings).resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _warning("local_trace.begin.error", exc, path=directory)
        return None

    started_at = _utc_now()
    active_path, final_path = _trace_paths(directory, trace_id, started_at)
    trace = LocalTrace(
        trace_id=trace_id,
        session_id=session_id,
        user_id=user_id,
        namespace=namespace,
        operation=operation,
        active_path=active_path,
        final_path=final_path,
        retention_count=settings.LOCAL_TRACE_RETENTION_COUNT,
        max_payload_bytes=settings.LOCAL_TRACE_MAX_PAYLOAD_BYTES,
        capture_content=settings.LOCAL_TRACE_CAPTURE_CONTENT,
        started_at=started_at,
        started_monotonic=perf_counter(),
    )
    trace.record(
        "trace.start",
        name=operation,
        status="started",
        payload={"request": request_payload},
    )
    return trace


@contextmanager
def activate_local_trace(trace: LocalTrace | None) -> Iterator[LocalTrace | None]:
    if trace is None:
        yield None
        return
    token = _ACTIVE_TRACE.set(trace)
    try:
        yield trace
    finally:
        _ACTIVE_TRACE.reset(token)


def active_local_trace(trace_id: str | None = None) -> LocalTrace | None:
    trace = _ACTIVE_TRACE.get()
    if trace is None or trace.finished:
        return None
    if trace_id is not None and trace.trace_id != trace_id:
        return None
    return trace


def record_local_application_event(event: str, payload: Mapping[str, Any]) -> None:
    trace = active_local_trace()
    if trace is None:
        return
    trace.record(
        "application.event",
        name=event,
        status=_event_status(event),
        payload=payload,
    )


def record_local_exception(exc: BaseException, *, name: str) -> None:
    trace = active_local_trace()
    if trace is not None:
        trace.record_exception(exc, name=name)


def _event_status(event: str) -> str | None:
    suffix = event.rsplit(".", 1)[-1]
    if suffix in {"started", "finished", "error", "interrupted", "blocked"}:
        return suffix
    return None


def _serialized_name(serialized: Mapping[str, Any] | None, fallback: str) -> str:
    if serialized:
        name = serialized.get("name")
        if isinstance(name, str) and name:
            return name
        identifier = serialized.get("id")
        if isinstance(identifier, Sequence) and not isinstance(identifier, str) and identifier:
            return str(identifier[-1])
    return fallback


class LocalTraceCallbackHandler(BaseCallbackHandler):
    run_inline = True
    raise_error = False

    def __init__(self, trace: LocalTrace) -> None:
        self.trace = trace
        self._starts: dict[str, float] = {}
        self._span_kinds: dict[str, str] = {}
        self._span_names: dict[str, str] = {}
        self._lock = threading.RLock()

    def _start(
        self,
        span_kind: str,
        name: str,
        run_id: UUID,
        parent_run_id: UUID | None,
        payload: Any,
    ) -> None:
        key = str(run_id)
        with self._lock:
            if key in self._starts:
                return
            self._starts[key] = perf_counter()
            self._span_kinds[key] = span_kind
            self._span_names[key] = name
        self.trace.record(
            "span.start",
            name=name,
            status="started",
            span_kind=span_kind,
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload=payload,
        )

    def _finish_fields(self, run_id: UUID) -> tuple[float | None, str | None, str | None]:
        key = str(run_id)
        with self._lock:
            started = self._starts.pop(key, None)
            span_kind = self._span_kinds.pop(key, None)
            span_name = self._span_names.pop(key, None)
        elapsed = None if started is None else round((perf_counter() - started) * 1000, 2)
        return elapsed, span_kind, span_name

    def _end(
        self,
        default_kind: str,
        name: str,
        run_id: UUID,
        parent_run_id: UUID | None,
        payload: Any,
    ) -> None:
        elapsed, span_kind, span_name = self._finish_fields(run_id)
        self.trace.record(
            "span.end",
            name=span_name or name,
            status="success",
            span_kind=span_kind or default_kind,
            run_id=run_id,
            parent_run_id=parent_run_id,
            elapsed_ms=elapsed,
            payload=payload,
        )

    def _error(
        self,
        default_kind: str,
        name: str,
        error: BaseException,
        run_id: UUID,
        parent_run_id: UUID | None,
    ) -> None:
        elapsed, span_kind, span_name = self._finish_fields(run_id)
        self.trace.record_exception(
            error,
            name=span_name or name,
            run_id=run_id,
            parent_run_id=parent_run_id,
            span_kind=span_kind or default_kind,
            elapsed_ms=elapsed,
        )

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        name = str(kwargs.get("name") or _serialized_name(serialized, "chain"))
        self._start(
            "chain",
            name,
            run_id,
            parent_run_id,
            {"serialized": serialized, "inputs": inputs, "tags": tags, "metadata": metadata},
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self._end("chain", str(kwargs.get("name") or "chain"), run_id, parent_run_id, {"outputs": outputs})

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self._error("chain", str(kwargs.get("name") or "chain"), error, run_id, parent_run_id)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self._start(
            "llm",
            _serialized_name(serialized, "chat_model"),
            run_id,
            parent_run_id,
            {"serialized": serialized, "messages": messages, "tags": tags, "metadata": metadata},
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self._start(
            "llm",
            _serialized_name(serialized, "llm"),
            run_id,
            parent_run_id,
            {"serialized": serialized, "prompts": prompts, "tags": tags, "metadata": metadata},
        )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self._end("llm", str(kwargs.get("name") or "llm"), run_id, parent_run_id, {"response": response})

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self._error("llm", str(kwargs.get("name") or "llm"), error, run_id, parent_run_id)

    def on_llm_new_token(self, token: str, **kwargs: Any) -> Any:
        return None

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self._start(
            "tool",
            _serialized_name(serialized, "tool"),
            run_id,
            parent_run_id,
            {
                "serialized": serialized,
                "input": inputs if inputs is not None else input_str,
                "tags": tags,
                "metadata": metadata,
            },
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self._end("tool", str(kwargs.get("name") or "tool"), run_id, parent_run_id, {"output": output})

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self._error("tool", str(kwargs.get("name") or "tool"), error, run_id, parent_run_id)

    def on_retriever_start(
        self,
        serialized: dict[str, Any],
        query: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self._start(
            "retriever",
            _serialized_name(serialized, "retriever"),
            run_id,
            parent_run_id,
            {"serialized": serialized, "query": query, "tags": tags, "metadata": metadata},
        )

    def on_retriever_end(
        self,
        documents: Sequence[Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self._end(
            "retriever",
            str(kwargs.get("name") or "retriever"),
            run_id,
            parent_run_id,
            {"documents": documents},
        )

    def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self._error("retriever", str(kwargs.get("name") or "retriever"), error, run_id, parent_run_id)

    def on_retry(
        self,
        retry_state: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        self.trace.record(
            "span.event",
            name="retry",
            status="retrying",
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload={"retry_state": retry_state},
        )


def build_local_trace_callback(settings: Settings, trace_id: str) -> LocalTraceCallbackHandler | None:
    if not settings.LOCAL_TRACE_ENABLED:
        return None
    trace = active_local_trace(trace_id)
    return LocalTraceCallbackHandler(trace) if trace is not None else None


def _terminal_status(event_name: str | None) -> str | None:
    return {
        "done": "success",
        "interrupt_required": "interrupted",
        "guardrail_blocked": "blocked",
        "error": "error",
        "no_pending_interrupt": "success",
    }.get(event_name or "")


async def trace_async_events(
    events: AsyncIterable[Any],
    trace: LocalTrace | None,
) -> AsyncIterable[Any]:
    if trace is None:
        async for event in events:
            yield event
        return

    iterator = aiter(events)
    terminal_status: str | None = None
    exhausted = False
    failure: BaseException | None = None
    try:
        while True:
            with activate_local_trace(trace):
                try:
                    event = await anext(iterator)
                except StopAsyncIteration:
                    exhausted = True
                    break
            terminal_status = _terminal_status(getattr(event, "event", None)) or terminal_status
            yield event
    except asyncio.CancelledError as exc:
        failure = exc
        with activate_local_trace(trace):
            trace.record_exception(exc, name="sse.stream.cancelled")
        raise
    except BaseException as exc:
        failure = exc
        with activate_local_trace(trace):
            trace.record_exception(exc, name="sse.stream")
        raise
    finally:
        if not trace.finished:
            status = terminal_status
            if status is None:
                status = "success" if exhausted else "cancelled"
            trace.finish(
                status,
                payload={"error": _exception_payload(failure)} if failure is not None else None,
            )
