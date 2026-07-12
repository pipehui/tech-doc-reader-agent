from collections.abc import AsyncIterable
from time import monotonic

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.sse import ServerSentEvent

from tech_doc_agent.app.api.schemas import (
    ApproveRequest,
    ChatRequest,
    HistoryViewResponse,
    SessionStateResponse,
)
from tech_doc_agent.app.api.tenant import resolve_request_tenant
from tech_doc_agent.app.api.sse import (
    aiter_with_trace_context,
    astream_parts_as_sse,
    event_source_response,
    iter_update_events,
    iter_with_trace_context,
    sse_event,
    stream_parts_as_sse,
)
from tech_doc_agent.app.core.guardrails import InputRisk, record_input_risk
from tech_doc_agent.app.core.observability import (
    get_trace_context,
    log_event,
    new_trace_id,
    trace_context,
)
from tech_doc_agent.app.runtime.chat_runtime import ChatRuntime


router = APIRouter()

__all__ = [
    "aiter_with_trace_context",
    "astream_parts_as_sse",
    "iter_update_events",
    "iter_with_trace_context",
    "router",
    "sse_event",
    "stream_parts_as_sse",
]


def get_runtime(request: Request) -> ChatRuntime:
    return request.app.state.runtime


def resolve_trace_id(body_trace_id: str | None, request: Request) -> str:
    return body_trace_id or request.headers.get("x-trace-id") or new_trace_id()


def _risk_payload(risk: InputRisk) -> dict:
    return {
        "risk_level": risk.level,
        "findings": [finding.name for finding in risk.findings],
    }


def _record_guardrail_decision(text: str, *, source: str) -> InputRisk:
    risk = record_input_risk(text, source=source)

    if risk.level == "medium":
        log_event(
            "guardrail.input_warning",
            source=source,
            **_risk_payload(risk),
        )
    elif risk.level == "high":
        log_event(
            "guardrail.input_blocked",
            source=source,
            **_risk_payload(risk),
        )

    return risk


def _guardrail_blocked_response(risk: InputRisk, *, session_id: str, source: str) -> JSONResponse:
    payload = {
        "error": "guardrail_blocked",
        "message": "Input was blocked by prompt-injection guardrails.",
        "session_id": session_id,
        "source": source,
        **_risk_payload(risk),
    }
    context = get_trace_context()
    for key in ("trace_id", "user_id", "namespace"):
        if context.get(key):
            payload[key] = context[key]

    return JSONResponse(status_code=400, content=payload)


def _guardrail_blocked_event(risk: InputRisk, *, session_id: str, source: str) -> ServerSentEvent:
    return sse_event(
        "guardrail_blocked",
        {
            "session_id": session_id,
            "source": source,
            **_risk_payload(risk),
        },
    )


def _request_guardrail_approval(
    runtime: ChatRuntime,
    session_id: str,
    message: str,
    risk: InputRisk,
    *,
    source: str,
    user_id: str | None = None,
    namespace: str | None = None,
) -> None:
    runtime.request_guardrail_approval(
        session_id,
        message,
        source=source,
        user_id=user_id,
        namespace=namespace,
        **_risk_payload(risk),
    )


def _guardrail_interrupt_event(risk: InputRisk, *, session_id: str, source: str) -> ServerSentEvent:
    return sse_event(
        "interrupt_required",
        {
            "session_id": session_id,
            "pending": True,
            "approval_kind": "guardrail_input",
            "source": source,
            **_risk_payload(risk),
        },
    )


async def astream_guardrail_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    risk: InputRisk,
    *,
    source: str,
    user_id: str | None = None,
    namespace: str | None = None,
) -> AsyncIterable[ServerSentEvent]:
    snapshot = await runtime.aget_session_state(session_id, user_id=user_id, namespace=namespace)
    yield sse_event("session_snapshot", snapshot)
    yield _guardrail_interrupt_event(risk, session_id=session_id, source=source)


async def astream_chat_events(
    runtime: ChatRuntime,
    session_id: str,
    message: str,
    user_id: str | None = None,
    namespace: str | None = None,
    guardrail_checked: bool = False,
    request_started_monotonic: float | None = None,
) -> AsyncIterable[ServerSentEvent]:
    request_started_monotonic = (
        request_started_monotonic
        if request_started_monotonic is not None
        else monotonic()
    )
    if not guardrail_checked:
        risk = _record_guardrail_decision(message, source="chat.message")
        if risk.level == "high":
            yield _guardrail_blocked_event(risk, session_id=session_id, source="chat.message")
            return
        if risk.level == "medium":
            _request_guardrail_approval(
                runtime,
                session_id,
                message,
                risk,
                source="chat.message",
                user_id=user_id,
                namespace=namespace,
            )
            async for event in astream_guardrail_approval_events(
                runtime,
                session_id,
                risk,
                source="chat.message",
                user_id=user_id,
                namespace=namespace,
            ):
                yield event
            return

    snapshot = await runtime.aget_session_state(session_id, user_id=user_id, namespace=namespace)
    yield sse_event("session_snapshot", snapshot)

    parts = runtime.astream_user_message(
        session_id,
        message,
        user_id=user_id,
        namespace=namespace,
        request_started_monotonic=request_started_monotonic,
    )
    async for event in astream_parts_as_sse(runtime, session_id, parts, user_id=user_id, namespace=namespace):
        yield event


async def astream_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    approved: bool,
    feedback: str = "",
    user_id: str | None = None,
    namespace: str | None = None,
    guardrail_checked: bool = False,
    request_started_monotonic: float | None = None,
) -> AsyncIterable[ServerSentEvent]:
    request_started_monotonic = (
        request_started_monotonic
        if request_started_monotonic is not None
        else monotonic()
    )
    if feedback and not guardrail_checked:
        risk = _record_guardrail_decision(feedback, source="chat.approval.feedback")
        if risk.level == "high":
            yield _guardrail_blocked_event(risk, session_id=session_id, source="chat.approval.feedback")
            return

    snapshot = await runtime.aget_session_state(session_id, user_id=user_id, namespace=namespace)
    yield sse_event("session_snapshot", snapshot)

    if not await runtime.ahas_pending_interrupt(session_id, user_id=user_id, namespace=namespace):
        log_event("chat.approval.no_pending_interrupt", approved=approved, async_runtime=True)
        yield sse_event(
            "no_pending_interrupt",
            {
                "session_id": session_id,
            },
        )
        return

    parts = runtime.astream_approval(
        session_id,
        approved,
        feedback,
        user_id=user_id,
        namespace=namespace,
        request_started_monotonic=request_started_monotonic,
    )
    async for event in astream_parts_as_sse(runtime, session_id, parts, user_id=user_id, namespace=namespace):
        yield event


@router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    request_started_monotonic = monotonic()
    runtime = get_runtime(request)
    trace_id = resolve_trace_id(body.trace_id, request)
    tenant = resolve_request_tenant(request, body.user_id, body.namespace)
    with trace_context(
        trace_id=trace_id,
        session_id=body.session_id,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        operation="chat",
    ):
        risk = _record_guardrail_decision(body.message, source="chat.message")
        if risk.level == "high":
            return _guardrail_blocked_response(risk, session_id=body.session_id, source="chat.message")
        if risk.level == "medium":
            _request_guardrail_approval(
                runtime,
                body.session_id,
                body.message,
                risk,
                source="chat.message",
                user_id=tenant.user_id,
                namespace=tenant.namespace,
            )
            return event_source_response(
                aiter_with_trace_context(
                    astream_guardrail_approval_events(
                        runtime,
                        body.session_id,
                        risk,
                        source="chat.message",
                        user_id=tenant.user_id,
                        namespace=tenant.namespace,
                    ),
                    trace_id,
                    body.session_id,
                    "chat",
                    user_id=tenant.user_id,
                    namespace=tenant.namespace,
                )
            )

    return event_source_response(
        aiter_with_trace_context(
            astream_chat_events(
                runtime,
                body.session_id,
                body.message,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
                guardrail_checked=True,
                request_started_monotonic=request_started_monotonic,
            ),
            trace_id,
            body.session_id,
            "chat",
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
    )


@router.post("/chat/approve")
async def approve(body: ApproveRequest, request: Request):
    request_started_monotonic = monotonic()
    runtime = get_runtime(request)
    trace_id = resolve_trace_id(body.trace_id, request)
    tenant = resolve_request_tenant(request, body.user_id, body.namespace)
    if body.feedback:
        with trace_context(
            trace_id=trace_id,
            session_id=body.session_id,
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            operation="approval",
        ):
            risk = _record_guardrail_decision(body.feedback, source="chat.approval.feedback")
            if risk.level == "high":
                return _guardrail_blocked_response(
                    risk,
                    session_id=body.session_id,
                    source="chat.approval.feedback",
                )

    return event_source_response(
        aiter_with_trace_context(
            astream_approval_events(
                runtime,
                body.session_id,
                body.approved,
                body.feedback,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
                guardrail_checked=True,
                request_started_monotonic=request_started_monotonic,
            ),
            trace_id,
            body.session_id,
            "approval",
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
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
