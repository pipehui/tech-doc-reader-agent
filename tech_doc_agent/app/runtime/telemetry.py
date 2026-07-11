from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from tech_doc_agent.app.core.observability import log_event, timed_node
from tech_doc_agent.app.core.tenant import TenantContext


def _elapsed_ms(start: float, clock: Callable[[], float]) -> float:
    return round((clock() - start) * 1000, 2)


def _error_message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


@dataclass(frozen=True, slots=True)
class OperationTrace:
    event_prefix: str
    phase: str
    session_id: str
    tenant: TenantContext
    started_at: float
    async_runtime: bool
    completion_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeOperationTelemetry:
    event_logger: Callable[..., None] = log_event
    timer_factory: Callable[..., AbstractContextManager[None]] = timed_node
    clock: Callable[[], float] = perf_counter

    def _base_fields(self, trace: OperationTrace, *, elapsed: bool = False) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "session_id": trace.session_id,
            "user_id": trace.tenant.user_id,
            "namespace": trace.tenant.namespace,
        }
        if elapsed:
            fields["elapsed_ms"] = _elapsed_ms(trace.started_at, self.clock)
        fields.update(trace.completion_fields)
        if trace.async_runtime:
            fields["async_runtime"] = True
        return fields

    def start_chat(
        self,
        session_id: str,
        tenant: TenantContext,
        *,
        message_length: int,
        async_runtime: bool,
    ) -> OperationTrace:
        trace = OperationTrace(
            event_prefix="chat.request",
            phase="chat",
            session_id=session_id,
            tenant=tenant,
            started_at=self.clock(),
            async_runtime=async_runtime,
        )
        self.event_logger(
            "chat.request.started",
            **self._base_fields(trace),
            message_length=message_length,
        )
        return trace

    def start_approval(
        self,
        session_id: str,
        tenant: TenantContext,
        *,
        approved: bool,
        async_runtime: bool,
    ) -> OperationTrace:
        trace = OperationTrace(
            event_prefix="chat.approval",
            phase="approval",
            session_id=session_id,
            tenant=tenant,
            started_at=self.clock(),
            async_runtime=async_runtime,
            completion_fields={"approved": approved},
        )
        self.event_logger("chat.approval.started", **self._base_fields(trace))
        return trace

    def stream_timer(self, trace: OperationTrace) -> AbstractContextManager[None]:
        timer_name = "graph.stream.thread" if trace.async_runtime else "graph.stream"
        return self.timer_factory(
            timer_name,
            phase=trace.phase,
            **trace.completion_fields,
        )

    def error(self, trace: OperationTrace, exc: Exception) -> None:
        self.event_logger(
            f"{trace.event_prefix}.error",
            **self._base_fields(trace, elapsed=True),
            error_type=type(exc).__name__,
            error=_error_message(exc),
        )

    def no_pending_interrupt(self, trace: OperationTrace) -> None:
        self.event_logger(
            "chat.approval.no_pending_interrupt",
            **self._base_fields(trace, elapsed=True),
        )

    def finish(self, trace: OperationTrace, *, pending_interrupt: bool) -> None:
        suffix = "interrupted" if pending_interrupt else "finished"
        self.event_logger(
            f"{trace.event_prefix}.{suffix}",
            **self._base_fields(trace, elapsed=True),
            pending_interrupt=pending_interrupt,
        )
