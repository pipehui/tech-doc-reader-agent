import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from langgraph.types import StateSnapshot

from tech_doc_agent.app.core.conversation_summary import (
    ConversationSummary,
    read_conversation_summary,
)
from tech_doc_agent.app.core.tenant import TenantContext, parse_tenant
from tech_doc_agent.app.runtime.serialization import MessageSerializer


class GraphProvider(Protocol):
    def __call__(self) -> Any: ...


class SessionConfigBuilder(Protocol):
    def __call__(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
        operation: str = "state",
        with_callbacks: bool = False,
        request_started_monotonic: float | None = None,
    ) -> dict[str, Any]: ...


class PendingGuardrailChecker(Protocol):
    def __call__(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class _SessionRead:
    tenant: TenantContext
    snapshot: StateSnapshot
    values: dict[str, Any]
    pending_guardrail: bool

    @property
    def pending_interrupt(self) -> bool:
        return self.pending_guardrail or bool(self.snapshot.next)


@dataclass(slots=True)
class SessionQueryService:
    """Read graph checkpoints and project them into API-facing session views."""

    graph_provider: GraphProvider
    config_builder: SessionConfigBuilder
    pending_guardrail_checker: PendingGuardrailChecker
    serializer: MessageSerializer = field(default_factory=MessageSerializer)

    def get_snapshot(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> StateSnapshot:
        return self.graph_provider().get_state(
            self.config_builder(session_id, user_id=user_id, namespace=namespace)
        )

    async def aget_snapshot(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> StateSnapshot:
        return await asyncio.to_thread(self.get_snapshot, session_id, user_id, namespace)

    def _read(
        self,
        session_id: str,
        user_id: str | None,
        namespace: str | None,
    ) -> _SessionRead:
        tenant = parse_tenant(user_id, namespace, prefer_context=True)
        pending_guardrail = self.pending_guardrail_checker(
            session_id,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
        snapshot = self.get_snapshot(
            session_id,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
        state_values = getattr(snapshot, "values", None)

        if not isinstance(state_values, dict):
            state_values = {}

        return _SessionRead(
            tenant=tenant,
            snapshot=snapshot,
            values=state_values,
            pending_guardrail=pending_guardrail,
        )

    def get_history(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        read = self._read(session_id, user_id, namespace)
        messages = read.values.get("messages", [])
        summary = read_conversation_summary(read.values.get("conversation_summary"))
        serialized_messages = []
        if summary is not None:
            serialized_messages.append(_summary_history_message(summary))
        serialized_messages.extend(self.serializer.serialize(message) for message in messages)

        return {
            "session_id": session_id,
            "user_id": read.values.get("user_id") or read.tenant.user_id,
            "namespace": read.values.get("namespace") or read.tenant.namespace,
            "learning_target": read.values.get("learning_target"),
            "pending_interrupt": read.pending_interrupt,
            "message_count": len(serialized_messages),
            "messages": serialized_messages,
        }

    def get_history_view(
        self,
        session_id: str,
        include_tools: bool = False,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        read = self._read(session_id, user_id, namespace)
        raw_messages = read.values.get("messages", [])
        items = []
        summary = read_conversation_summary(read.values.get("conversation_summary"))
        if summary is not None:
            items.append(_summary_history_view_item(summary))

        for message in raw_messages:
            item = self.serializer.to_history_view_item(message)
            if item is None:
                continue

            if item["role"] == "tool" and not include_tools:
                continue

            items.append(item)

        return {
            "session_id": session_id,
            "user_id": read.values.get("user_id") or read.tenant.user_id,
            "namespace": read.values.get("namespace") or read.tenant.namespace,
            "learning_target": read.values.get("learning_target"),
            "pending_interrupt": read.pending_interrupt,
            "message_count": len(items),
            "messages": items,
        }

    async def aget_history_view(
        self,
        session_id: str,
        include_tools: bool = False,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.get_history_view,
            session_id,
            include_tools=include_tools,
            user_id=user_id,
            namespace=namespace,
        )

    def get_session_state(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        read = self._read(session_id, user_id, namespace)
        messages = read.values.get("messages", [])
        summary = read_conversation_summary(read.values.get("conversation_summary"))
        learning_target = read.values.get("learning_target")
        exists = bool(messages) or summary is not None or bool(learning_target) or read.pending_interrupt

        dialog_stack = read.values.get("dialog_state", [])
        current_agent = (
            "guardrail"
            if read.pending_guardrail
            else dialog_stack[-1]
            if dialog_stack
            else "primary"
        )

        return {
            "session_id": session_id,
            "user_id": read.values.get("user_id") or read.tenant.user_id,
            "namespace": read.values.get("namespace") or read.tenant.namespace,
            "exists": exists,
            "pending_interrupt": read.pending_interrupt,
            "learning_target": learning_target,
            "message_count": len(messages) + int(summary is not None),
            "current_agent": current_agent,
            "workflow_plan": read.values.get("workflow_plan", []),
            "plan_index": read.values.get("plan_index", 0),
            "budget_usage": read.values.get("budget_usage"),
            "budget_status": read.values.get("budget_status"),
            "budget_termination": read.values.get("budget_termination") or None,
            "context_metrics": read.values.get("context_metrics"),
        }

    async def aget_session_state(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self.get_session_state, session_id, user_id, namespace)


def _summary_history_message(summary: ConversationSummary) -> dict[str, Any]:
    return {
        "id": f"conversation-summary-{summary.summary_id}",
        "role": "system",
        "raw_type": "conversation_summary",
        "content": summary.content,
        "name": "conversation_summary",
        "tool_call_id": None,
        "tool_calls": [],
    }


def _summary_history_view_item(summary: ConversationSummary) -> dict[str, Any]:
    return {
        "id": f"conversation-summary-{summary.summary_id}",
        "role": "system",
        "kind": "conversation_summary",
        "content": summary.content,
        "name": "conversation_summary",
        "tool_call_id": None,
    }
