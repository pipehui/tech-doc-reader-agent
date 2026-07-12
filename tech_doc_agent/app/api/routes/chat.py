from time import monotonic

from fastapi import APIRouter, Request

from tech_doc_agent.app.api import chat_delivery as _chat_delivery
from tech_doc_agent.app.api.schemas import (
    ApproveRequest,
    ChatRequest,
    HistoryViewResponse,
    SessionStateResponse,
)
from tech_doc_agent.app.api.tenant import resolve_request_tenant
from tech_doc_agent.app.core.observability import new_trace_id
from tech_doc_agent.app.runtime.chat_runtime import ChatRuntime


router = APIRouter()


def get_runtime(request: Request) -> ChatRuntime:
    return request.app.state.runtime


def resolve_trace_id(body_trace_id: str | None, request: Request) -> str:
    return body_trace_id or request.headers.get("x-trace-id") or new_trace_id()


@router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    request_started_monotonic = monotonic()
    runtime = get_runtime(request)
    trace_id = resolve_trace_id(body.trace_id, request)
    tenant = resolve_request_tenant(request, body.user_id, body.namespace)
    return _chat_delivery.chat_response(
        runtime,
        session_id=body.session_id,
        message=body.message,
        trace_id=trace_id,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        request_started_monotonic=request_started_monotonic,
    )


@router.post("/chat/approve")
async def approve(body: ApproveRequest, request: Request):
    request_started_monotonic = monotonic()
    runtime = get_runtime(request)
    trace_id = resolve_trace_id(body.trace_id, request)
    tenant = resolve_request_tenant(request, body.user_id, body.namespace)
    return _chat_delivery.approval_response(
        runtime,
        session_id=body.session_id,
        approved=body.approved,
        feedback=body.feedback,
        trace_id=trace_id,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        request_started_monotonic=request_started_monotonic,
    )


@router.get("/sessions/{session_id}/history", response_model=HistoryViewResponse)
async def get_history(
    session_id: str,
    request: Request,
    include_tools: bool = False,
    user_id: str | None = None,
    namespace: str | None = None,
):
    runtime = get_runtime(request)
    tenant = resolve_request_tenant(request, user_id, namespace)
    history = await runtime.aget_history_view(
        session_id,
        include_tools=include_tools,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    return HistoryViewResponse(**history)


@router.get("/sessions/{session_id}/state", response_model=SessionStateResponse)
async def get_session_state(
    session_id: str,
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
):
    runtime = get_runtime(request)
    tenant = resolve_request_tenant(request, user_id, namespace)
    state = await runtime.aget_session_state(
        session_id,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    return SessionStateResponse(**state)
