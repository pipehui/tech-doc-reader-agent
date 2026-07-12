from collections.abc import AsyncIterable
from time import monotonic

from fastapi.responses import JSONResponse, Response
from fastapi.sse import ServerSentEvent

from tech_doc_agent.app.api import sse as _sse
from tech_doc_agent.app.application.input_guardrails import evaluate_input_guardrail
from tech_doc_agent.app.core.guardrails import InputRisk
from tech_doc_agent.app.core.observability import (
    get_trace_context,
    log_event,
    trace_context,
)
from tech_doc_agent.app.runtime.chat_runtime import ChatRuntime


def _risk_payload(risk: InputRisk) -> dict:
    return {
        "risk_level": risk.level,
        "findings": [finding.name for finding in risk.findings],
    }


def _guardrail_blocked_response(
    risk: InputRisk,
    *,
    session_id: str,
    source: str,
) -> JSONResponse:
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


def _guardrail_blocked_event(
    risk: InputRisk,
    *,
    session_id: str,
    source: str,
) -> ServerSentEvent:
    return _sse.sse_event(
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


def _guardrail_interrupt_event(
    risk: InputRisk,
    *,
    session_id: str,
    source: str,
) -> ServerSentEvent:
    return _sse.sse_event(
        "interrupt_required",
        {
            "session_id": session_id,
            "pending": True,
            "approval_kind": "guardrail_input",
            "source": source,
            **_risk_payload(risk),
        },
    )


async def _astream_guardrail_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    risk: InputRisk,
    *,
    source: str,
    user_id: str | None = None,
    namespace: str | None = None,
) -> AsyncIterable[ServerSentEvent]:
    snapshot = await runtime.aget_session_state(
        session_id,
        user_id=user_id,
        namespace=namespace,
    )
    yield _sse.sse_event("session_snapshot", snapshot)
    yield _guardrail_interrupt_event(risk, session_id=session_id, source=source)


async def _astream_chat_events(
    runtime: ChatRuntime,
    session_id: str,
    message: str,
    user_id: str | None = None,
    namespace: str | None = None,
    request_started_monotonic: float | None = None,
) -> AsyncIterable[ServerSentEvent]:
    request_started_monotonic = (
        request_started_monotonic
        if request_started_monotonic is not None
        else monotonic()
    )
    snapshot = await runtime.aget_session_state(
        session_id,
        user_id=user_id,
        namespace=namespace,
    )
    yield _sse.sse_event("session_snapshot", snapshot)

    parts = runtime.astream_user_message(
        session_id,
        message,
        user_id=user_id,
        namespace=namespace,
        request_started_monotonic=request_started_monotonic,
    )
    async for event in _sse.astream_parts_as_sse(
        runtime,
        session_id,
        parts,
        user_id=user_id,
        namespace=namespace,
    ):
        yield event


async def _astream_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    approved: bool,
    feedback: str = "",
    user_id: str | None = None,
    namespace: str | None = None,
    request_started_monotonic: float | None = None,
) -> AsyncIterable[ServerSentEvent]:
    request_started_monotonic = (
        request_started_monotonic
        if request_started_monotonic is not None
        else monotonic()
    )
    snapshot = await runtime.aget_session_state(
        session_id,
        user_id=user_id,
        namespace=namespace,
    )
    yield _sse.sse_event("session_snapshot", snapshot)

    if not await runtime.ahas_pending_interrupt(
        session_id,
        user_id=user_id,
        namespace=namespace,
    ):
        log_event(
            "chat.approval.no_pending_interrupt",
            approved=approved,
            async_runtime=True,
        )
        yield _sse.sse_event(
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
    async for event in _sse.astream_parts_as_sse(
        runtime,
        session_id,
        parts,
        user_id=user_id,
        namespace=namespace,
    ):
        yield event


def _stream_response(
    events: AsyncIterable[ServerSentEvent],
    *,
    trace_id: str,
    session_id: str,
    operation: str,
    user_id: str,
    namespace: str,
) -> Response:
    return _sse.event_source_response(
        _sse.aiter_with_trace_context(
            events,
            trace_id,
            session_id,
            operation,
            user_id=user_id,
            namespace=namespace,
        )
    )


def chat_response(
    runtime: ChatRuntime,
    *,
    session_id: str,
    message: str,
    trace_id: str,
    user_id: str,
    namespace: str,
    request_started_monotonic: float,
) -> Response:
    with trace_context(
        trace_id=trace_id,
        session_id=session_id,
        user_id=user_id,
        namespace=namespace,
        operation="chat",
    ):
        risk = evaluate_input_guardrail(message, source="chat.message")
        if risk.level == "high":
            return _guardrail_blocked_response(
                risk,
                session_id=session_id,
                source="chat.message",
            )
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
            events = _astream_guardrail_approval_events(
                runtime,
                session_id,
                risk,
                source="chat.message",
                user_id=user_id,
                namespace=namespace,
            )
        else:
            events = _astream_chat_events(
                runtime,
                session_id,
                message,
                user_id=user_id,
                namespace=namespace,
                request_started_monotonic=request_started_monotonic,
            )

        return _stream_response(
            events,
            trace_id=trace_id,
            session_id=session_id,
            operation="chat",
            user_id=user_id,
            namespace=namespace,
        )


def approval_response(
    runtime: ChatRuntime,
    *,
    session_id: str,
    approved: bool,
    feedback: str,
    trace_id: str,
    user_id: str,
    namespace: str,
    request_started_monotonic: float,
) -> Response:
    with trace_context(
        trace_id=trace_id,
        session_id=session_id,
        user_id=user_id,
        namespace=namespace,
        operation="approval",
    ):
        if feedback:
            risk = evaluate_input_guardrail(
                feedback,
                source="chat.approval.feedback",
            )
            if risk.level == "high":
                return _guardrail_blocked_response(
                    risk,
                    session_id=session_id,
                    source="chat.approval.feedback",
                )

        events = _astream_approval_events(
            runtime,
            session_id,
            approved,
            feedback,
            user_id=user_id,
            namespace=namespace,
            request_started_monotonic=request_started_monotonic,
        )
        return _stream_response(
            events,
            trace_id=trace_id,
            session_id=session_id,
            operation="approval",
            user_id=user_id,
            namespace=namespace,
        )
