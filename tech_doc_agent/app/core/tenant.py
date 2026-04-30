from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from tech_doc_agent.app.core.observability import get_trace_context
from tech_doc_agent.app.services.retrieval.metadata import DEFAULT_NAMESPACE, DEFAULT_USER_ID


TENANT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_TENANT_ID_RE = re.compile(TENANT_ID_PATTERN)


@dataclass(frozen=True)
class TenantContext:
    user_id: str = DEFAULT_USER_ID
    namespace: str = DEFAULT_NAMESPACE

    @property
    def thread_prefix(self) -> str:
        return f"{self.user_id}:{self.namespace}"


def normalize_tenant_value(value: Any, default: str) -> str:
    if value is None:
        return default

    text = str(value).strip()
    if not text:
        return default

    return text if _TENANT_ID_RE.fullmatch(text) else default


def tenant_from_values(
    user_id: Any = None,
    namespace: Any = None,
    *,
    prefer_context: bool = False,
) -> TenantContext:
    if prefer_context:
        context = get_trace_context()
        user_id = context.get("user_id") or user_id
        namespace = context.get("namespace") or namespace

    return TenantContext(
        user_id=normalize_tenant_value(user_id, DEFAULT_USER_ID),
        namespace=normalize_tenant_value(namespace, DEFAULT_NAMESPACE),
    )


def current_tenant(
    *,
    fallback_user_id: Any = None,
    fallback_namespace: Any = None,
) -> TenantContext:
    return tenant_from_values(
        fallback_user_id,
        fallback_namespace,
        prefer_context=True,
    )


def tenant_thread_id(session_id: str, tenant: TenantContext) -> str:
    return f"{tenant.thread_prefix}:{session_id}"
