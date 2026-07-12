from dataclasses import dataclass
from typing import Any

from tech_doc_agent.app.core.langfuse_tracing import build_langfuse_trace, langfuse_metadata
from tech_doc_agent.app.core.observability import get_trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.tenant import parse_tenant, tenant_thread_id


@dataclass(frozen=True, slots=True)
class SessionConfigFactory:
    """Build tenant-scoped LangGraph configuration for one runtime operation."""

    settings: Settings

    def build(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
        operation: str = "state",
        with_callbacks: bool = False,
    ) -> dict[str, Any]:
        tenant = parse_tenant(user_id, namespace, prefer_context=True)
        context = get_trace_context()
        trace_id = context.get("trace_id")
        langfuse_trace = (
            build_langfuse_trace(self.settings, trace_id)
            if with_callbacks and isinstance(trace_id, str)
            else None
        )
        metadata = {
            "session_id": session_id,
            "user_id": tenant.user_id,
            "namespace": tenant.namespace,
            **langfuse_metadata(
                session_id=session_id,
                operation=operation,
                external_trace_id=trace_id if isinstance(trace_id, str) else None,
                langfuse_trace=langfuse_trace,
            ),
        }

        config: dict[str, Any] = {
            "configurable": {
                "thread_id": tenant_thread_id(session_id, tenant),
            },
            "metadata": metadata,
            "run_name": f"tech_doc_agent.{operation}",
            "recursion_limit": self.settings.LANGGRAPH_RECURSION_LIMIT,
        }

        if langfuse_trace is not None:
            config["callbacks"] = [langfuse_trace.callback]

        return config
