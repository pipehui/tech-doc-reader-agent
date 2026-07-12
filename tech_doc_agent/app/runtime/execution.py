import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Protocol

from langchain_core.messages import ToolMessage
from langgraph.types import StateSnapshot

from tech_doc_agent.app.core.errors import PermissionDenied
from tech_doc_agent.app.core.langfuse_tracing import flush_langfuse
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.tenant import TenantContext, parse_tenant
from tech_doc_agent.app.runtime.approvals import ApprovalService
from tech_doc_agent.app.runtime.sessions import GraphProvider, SessionConfigBuilder, SessionQueryService
from tech_doc_agent.app.runtime.telemetry import OperationTrace, RuntimeOperationTelemetry


_STREAM_DONE = object()


class SettingsProvider(Protocol):
    def __call__(self) -> Settings: ...


def _next_or_done(iterator: Iterator[Any]) -> Any:
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_DONE


async def _aiter_sync_iterator(parts: Iterator[Any]) -> AsyncIterator[Any]:
    iterator = iter(parts)

    try:
        while True:
            part = await asyncio.to_thread(_next_or_done, iterator)
            if part is _STREAM_DONE:
                return
            yield part
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            with suppress(Exception):
                await asyncio.to_thread(close)


def _interrupted_node(snapshot: StateSnapshot) -> str | None:
    next_nodes = getattr(snapshot, "next", ()) or ()
    return next_nodes[0] if next_nodes else None


def _rejection_tool_message(snapshot: StateSnapshot, feedback: str) -> ToolMessage:
    tool_call_id = snapshot.values["messages"][-1].tool_calls[0]["id"]
    feedback = feedback or "用户未提供原因"
    content = f"用户拒绝了此操作。原因：'{feedback}'。请根据用户的反馈继续协助。"
    error = PermissionDenied(
        "The user rejected this tool execution.",
        code="tool_execution_rejected",
        cause_type="UserDecision",
    )
    return ToolMessage(
        tool_call_id=tool_call_id,
        status="error",
        content=content,
        artifact={"error": error.to_payload()},
    )


@dataclass(slots=True)
class GraphExecutionService:
    settings_provider: SettingsProvider
    graph_provider: GraphProvider
    config_builder: SessionConfigBuilder
    session_queries: SessionQueryService
    approvals: ApprovalService
    telemetry: RuntimeOperationTelemetry = field(default_factory=RuntimeOperationTelemetry)
    monotonic_clock: Callable[[], float] = monotonic

    def _graph_input(self, user_input: str, tenant: TenantContext) -> dict[str, Any]:
        return {
            "messages": [("user", user_input)],
            "user_id": tenant.user_id,
            "namespace": tenant.namespace,
        }

    def _flush_if_configured(self) -> None:
        settings = self.settings_provider()
        if settings.LANGFUSE_FLUSH_ON_REQUEST:
            flush_langfuse(settings)

    def _finish_operation(self, trace: OperationTrace) -> None:
        pending_interrupt = self.has_pending_interrupt(
            trace.session_id,
            user_id=trace.tenant.user_id,
            namespace=trace.tenant.namespace,
        )
        self.telemetry.finish(trace, pending_interrupt=pending_interrupt)
        self._flush_if_configured()

    def has_pending_interrupt(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        if self.approvals.has_pending_guardrail_approval(
            session_id,
            user_id=user_id,
            namespace=namespace,
        ):
            return True
        snapshot = self.session_queries.get_snapshot(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )
        return bool(snapshot.next)

    async def ahas_pending_interrupt(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self.has_pending_interrupt,
            session_id,
            user_id,
            namespace,
        )

    def _stream_user_message(
        self,
        session_id: str,
        user_input: str,
        user_id: str | None,
        namespace: str | None,
        *,
        async_runtime: bool,
        request_started_monotonic: float | None = None,
    ) -> Iterator[Any]:
        request_started_monotonic = (
            request_started_monotonic
            if request_started_monotonic is not None
            else self.monotonic_clock()
        )
        tenant = parse_tenant(user_id, namespace, prefer_context=True)
        trace = self.telemetry.start_chat(
            session_id,
            tenant,
            message_length=len(user_input),
            async_runtime=async_runtime,
        )

        try:
            with self.telemetry.stream_timer(trace):
                graph = self.graph_provider()
                config = self.config_builder(
                    session_id,
                    user_id=tenant.user_id,
                    namespace=tenant.namespace,
                    operation="chat",
                    with_callbacks=True,
                    request_started_monotonic=request_started_monotonic,
                )
                yield from graph.stream(
                    self._graph_input(user_input, tenant),
                    config,
                    stream_mode=["messages", "updates"],
                    version="v2",
                )
        except Exception as exc:
            self.telemetry.error(trace, exc)
            raise

        self._finish_operation(trace)

    def stream_user_message(
        self,
        session_id: str,
        user_input: str,
        user_id: str | None = None,
        namespace: str | None = None,
        request_started_monotonic: float | None = None,
    ) -> Iterator[Any]:
        yield from self._stream_user_message(
            session_id,
            user_input,
            user_id,
            namespace,
            async_runtime=False,
            request_started_monotonic=request_started_monotonic,
        )

    async def astream_user_message(
        self,
        session_id: str,
        user_input: str,
        user_id: str | None = None,
        namespace: str | None = None,
        request_started_monotonic: float | None = None,
    ) -> AsyncIterator[Any]:
        async for part in _aiter_sync_iterator(
            self._stream_user_message(
                session_id,
                user_input,
                user_id,
                namespace,
                async_runtime=True,
                request_started_monotonic=request_started_monotonic,
            )
        ):
            yield part

    def _stream_approval(
        self,
        session_id: str,
        approved: bool,
        feedback: str,
        user_id: str | None,
        namespace: str | None,
        *,
        async_runtime: bool,
        request_started_monotonic: float | None = None,
    ) -> Iterator[Any]:
        request_started_monotonic = (
            request_started_monotonic
            if request_started_monotonic is not None
            else self.monotonic_clock()
        )
        tenant = parse_tenant(user_id, namespace, prefer_context=True)
        trace = self.telemetry.start_approval(
            session_id,
            tenant,
            approved=approved,
            async_runtime=async_runtime,
        )

        try:
            pending_guardrail = self.approvals.pop_pending_guardrail_approval(
                session_id,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
            )
            if pending_guardrail is not None:
                self.approvals.log_resolved(
                    pending_guardrail,
                    approved=approved,
                    feedback=feedback,
                )
                if approved:
                    yield from self._stream_user_message(
                        session_id,
                        pending_guardrail.user_input,
                        tenant.user_id,
                        tenant.namespace,
                        async_runtime=async_runtime,
                        request_started_monotonic=request_started_monotonic,
                    )
                else:
                    yield self.approvals.rejection_part(pending_guardrail, feedback)
                return

            snapshot = self.session_queries.get_snapshot(
                session_id,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
            )

            if not snapshot.next:
                self.telemetry.no_pending_interrupt(trace)
                return

            config = self.config_builder(
                session_id,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
                operation="approval",
                with_callbacks=True,
                request_started_monotonic=request_started_monotonic,
            )
            graph = self.graph_provider()

            if not approved:
                config = graph.update_state(
                    config,
                    {"messages": [_rejection_tool_message(snapshot, feedback)]},
                    as_node=_interrupted_node(snapshot),
                )

            with self.telemetry.stream_timer(trace):
                yield from graph.stream(
                    None,
                    config,
                    stream_mode=["messages", "updates"],
                    version="v2",
                )
        except Exception as exc:
            self.telemetry.error(trace, exc)
            raise

        self._finish_operation(trace)

    def stream_approval(
        self,
        session_id: str,
        approved: bool,
        feedback: str = "",
        user_id: str | None = None,
        namespace: str | None = None,
        request_started_monotonic: float | None = None,
    ) -> Iterator[Any]:
        yield from self._stream_approval(
            session_id,
            approved,
            feedback,
            user_id,
            namespace,
            async_runtime=False,
            request_started_monotonic=request_started_monotonic,
        )

    async def astream_approval(
        self,
        session_id: str,
        approved: bool,
        feedback: str = "",
        user_id: str | None = None,
        namespace: str | None = None,
        request_started_monotonic: float | None = None,
    ) -> AsyncIterator[Any]:
        async for part in _aiter_sync_iterator(
            self._stream_approval(
                session_id,
                approved,
                feedback,
                user_id,
                namespace,
                async_runtime=True,
                request_started_monotonic=request_started_monotonic,
            )
        ):
            yield part
