from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

from tech_doc_agent.app.core.execution_budget import (
    REQUEST_BUDGET_METADATA_KEY,
    build_execution_budget,
)
from tech_doc_agent.app.core.langfuse_tracing import build_langfuse_trace, langfuse_metadata
from tech_doc_agent.app.core.local_tracing import build_local_trace_callback
from tech_doc_agent.app.core.observability import get_trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.tenant import parse_tenant, tenant_thread_id


@dataclass(frozen=True, slots=True)
class SessionConfigFactory:
    """Build tenant-scoped LangGraph configuration for one runtime operation."""

    settings: Settings
    monotonic_clock: Callable[[], float] = monotonic
    execution_identity_metadata: Mapping[str, Any] | None = None

    def build(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
        operation: str = "state",
        with_callbacks: bool = False,
        request_started_monotonic: float | None = None,
    ) -> dict[str, Any]:
        tenant = parse_tenant(user_id, namespace, prefer_context=True)
        context = get_trace_context()
        trace_id = context.get("trace_id")
        langfuse_trace = (
            build_langfuse_trace(self.settings, trace_id)
            if with_callbacks and isinstance(trace_id, str)
            else None
        )
        local_trace_callback = (
            build_local_trace_callback(self.settings, trace_id)
            if with_callbacks and isinstance(trace_id, str)
            else None
        )
        metadata = {
            "session_id": session_id,
            "user_id": tenant.user_id,
            "namespace": tenant.namespace,
            "runtime_operation": operation,
            **langfuse_metadata(
                session_id=session_id,
                operation=operation,
                external_trace_id=trace_id if isinstance(trace_id, str) else None,
                langfuse_trace=langfuse_trace,
            ),
        }
        if self.execution_identity_metadata is not None:
            metadata["runtime_execution_identity"] = dict(
                self.execution_identity_metadata
            )
        if operation in {"chat", "approval"}:
            request_window = build_execution_budget(self.settings).start_request(
                now=(
                    request_started_monotonic
                    if request_started_monotonic is not None
                    else self.monotonic_clock()
                )
            )
            if request_window is not None:
                metadata[REQUEST_BUDGET_METADATA_KEY] = request_window.to_metadata()

        config: dict[str, Any] = {
            "configurable": {
                "thread_id": tenant_thread_id(session_id, tenant),
            },
            "metadata": metadata,
            "run_name": f"tech_doc_agent.{operation}",
            "recursion_limit": self.settings.LANGGRAPH_RECURSION_LIMIT,
        }

        callbacks = []
        if local_trace_callback is not None:
            callbacks.append(local_trace_callback)
        if langfuse_trace is not None:
            callbacks.append(langfuse_trace.callback)
        if callbacks:
            config["callbacks"] = callbacks

        return config
