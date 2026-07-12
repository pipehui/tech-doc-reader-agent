from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Protocol

from langchain_core.messages import AIMessage

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.tenant import TenantContext, parse_tenant, tenant_thread_id


@dataclass(frozen=True, slots=True)
class GuardrailApprovalRequest:
    session_id: str
    user_input: str
    user_id: str
    namespace: str
    source: str
    risk_level: str
    findings: tuple[str, ...]


class ApprovalRepository(Protocol):
    def put(self, key: str, request: GuardrailApprovalRequest) -> None: ...

    def get(self, key: str) -> GuardrailApprovalRequest | None: ...

    def pop(self, key: str) -> GuardrailApprovalRequest | None: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class InMemoryApprovalRepository:
    """Process-local adapter retained until a durable repository is configured."""

    _items: dict[str, GuardrailApprovalRequest] = field(default_factory=dict, init=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def put(self, key: str, request: GuardrailApprovalRequest) -> None:
        with self._lock:
            self._items[key] = request

    def get(self, key: str) -> GuardrailApprovalRequest | None:
        with self._lock:
            return self._items.get(key)

    def pop(self, key: str) -> GuardrailApprovalRequest | None:
        with self._lock:
            return self._items.pop(key, None)

    def close(self) -> None:
        return None


@dataclass(slots=True)
class ApprovalService:
    repository: ApprovalRepository
    event_logger: Callable[..., None] = log_event

    def _key(self, session_id: str, tenant: TenantContext) -> str:
        return tenant_thread_id(session_id, tenant)

    def request_guardrail_approval(
        self,
        session_id: str,
        user_input: str,
        *,
        source: str,
        risk_level: str,
        findings: list[str] | tuple[str, ...],
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> GuardrailApprovalRequest:
        tenant = parse_tenant(user_id, namespace, prefer_context=True)
        request = GuardrailApprovalRequest(
            session_id=session_id,
            user_input=user_input,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            source=source,
            risk_level=risk_level,
            findings=tuple(findings),
        )
        self.repository.put(self._key(session_id, tenant), request)
        self.event_logger(
            "guardrail.approval.requested",
            session_id=session_id,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            source=source,
            risk_level=risk_level,
            findings=list(findings),
        )
        return request

    def get_pending_guardrail_approval(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> GuardrailApprovalRequest | None:
        tenant = parse_tenant(user_id, namespace, prefer_context=True)
        return self.repository.get(self._key(session_id, tenant))

    def has_pending_guardrail_approval(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        return (
            self.get_pending_guardrail_approval(
                session_id,
                user_id=user_id,
                namespace=namespace,
            )
            is not None
        )

    def pop_pending_guardrail_approval(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> GuardrailApprovalRequest | None:
        tenant = parse_tenant(user_id, namespace, prefer_context=True)
        return self.repository.pop(self._key(session_id, tenant))

    def rejection_part(
        self,
        pending: GuardrailApprovalRequest,
        feedback: str,
    ) -> tuple[str, dict]:
        reason = feedback or "未提供原因"
        return (
            "updates",
            {
                "guardrail": {
                    "messages": [
                        AIMessage(
                            content=(
                                "这条输入被 guardrails 标记为 medium risk，审批未通过，"
                                f"已停止执行。原因：{reason}"
                            ),
                            name="guardrail",
                        )
                    ]
                }
            },
        )

    def log_resolved(
        self,
        pending: GuardrailApprovalRequest,
        *,
        approved: bool,
        feedback: str,
    ) -> None:
        self.event_logger(
            "guardrail.approval.resolved",
            session_id=pending.session_id,
            user_id=pending.user_id,
            namespace=pending.namespace,
            source=pending.source,
            risk_level=pending.risk_level,
            findings=list(pending.findings),
            approved=approved,
            feedback_length=len(feedback),
        )
