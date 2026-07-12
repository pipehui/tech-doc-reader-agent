from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.observability import get_trace_context


DEFAULT_USER_ID = "default"
DEFAULT_NAMESPACE = "tech_docs"
TENANT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_TENANT_ID_RE = re.compile(TENANT_ID_PATTERN)


@dataclass(frozen=True)
class TenantContext:
    user_id: str = DEFAULT_USER_ID
    namespace: str = DEFAULT_NAMESPACE

    def __post_init__(self) -> None:
        _parse_tenant_value(self.user_id, DEFAULT_USER_ID, "user_id")
        _parse_tenant_value(self.namespace, DEFAULT_NAMESPACE, "namespace")

    @property
    def thread_prefix(self) -> str:
        return f"{self.user_id}:{self.namespace}"


def _normalize_tenant_value(value: Any, default: str) -> str:
    if value is None:
        return default

    text = str(value).strip()
    if not text:
        return default

    return text if _TENANT_ID_RE.fullmatch(text) else default


def _parse_tenant_value(value: Any, default: str, field: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or _TENANT_ID_RE.fullmatch(value) is None:
        raise ValidationError(
            "The tenant identifier is invalid.",
            code="invalid_tenant",
            dependency="tenant_context",
            cause_type=f"InvalidTenant{field.title().replace('_', '')}",
        )
    return value


def normalize_tenant(
    user_id: Any = None,
    namespace: Any = None,
) -> TenantContext:
    return TenantContext(
        user_id=_normalize_tenant_value(user_id, DEFAULT_USER_ID),
        namespace=_normalize_tenant_value(namespace, DEFAULT_NAMESPACE),
    )


def parse_tenant(
    user_id: Any = None,
    namespace: Any = None,
    *,
    prefer_context: bool = False,
) -> TenantContext:
    if prefer_context:
        context = get_trace_context()
        if context.get("user_id") is not None:
            user_id = context["user_id"]
        if context.get("namespace") is not None:
            namespace = context["namespace"]

    return TenantContext(
        user_id=_parse_tenant_value(user_id, DEFAULT_USER_ID, "user_id"),
        namespace=_parse_tenant_value(
            namespace,
            DEFAULT_NAMESPACE,
            "namespace",
        ),
    )


def current_tenant(
    *,
    fallback_user_id: Any = None,
    fallback_namespace: Any = None,
) -> TenantContext:
    return parse_tenant(
        fallback_user_id,
        fallback_namespace,
        prefer_context=True,
    )


def tenant_from_config(config: Any) -> TenantContext:
    """Resolve tenant from a LangGraph RunnableConfig metadata.

    Priority: config["metadata"]["user_id"] / namespace > ContextVar > DEFAULT.

    Tools should call this helper instead of `current_tenant()` so that the
    tenant is carried explicitly through LangGraph's config plumbing rather
    than relying on the implicit ContextVar copy across threads.
    """
    metadata: dict[str, Any] = {}
    if isinstance(config, dict):
        raw_metadata = config.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata

    context = get_trace_context()
    user_id = (
        metadata.get("user_id")
        if metadata.get("user_id") is not None
        else context.get("user_id")
    )
    namespace = (
        metadata.get("namespace")
        if metadata.get("namespace") is not None
        else context.get("namespace")
    )

    return parse_tenant(user_id, namespace)


def session_id_from_config(config: Any) -> str | None:
    """Resolve session_id from a LangGraph RunnableConfig metadata.

    Falls back to the trace ContextVar so legacy code paths keep working.
    """
    metadata: dict[str, Any] = {}
    if isinstance(config, dict):
        raw_metadata = config.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata

    candidate = metadata.get("session_id")
    if candidate is None:
        candidate = get_trace_context().get("session_id")

    if candidate is None:
        return None
    text = str(candidate).strip()
    return text or None


def tenant_thread_id(session_id: str, tenant: TenantContext) -> str:
    return f"{tenant.thread_prefix}:{session_id}"
