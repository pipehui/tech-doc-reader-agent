from __future__ import annotations

from fastapi import HTTPException, Request

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.tenant import TenantContext, parse_tenant


def resolve_request_tenant(
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
) -> TenantContext:
    """Resolve the temporary dev tenant boundary without silent fallback."""

    candidate_user_id = (
        user_id if user_id is not None else request.headers.get("x-user-id")
    )
    candidate_namespace = (
        namespace
        if namespace is not None
        else request.headers.get("x-namespace")
    )
    try:
        return parse_tenant(candidate_user_id, candidate_namespace)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": exc.code,
                "message": exc.safe_message,
            },
        ) from exc


__all__ = ["resolve_request_tenant"]
