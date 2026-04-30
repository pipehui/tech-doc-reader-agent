from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from tech_doc_agent.app.services.chat_runtime import ChatRuntime
from fastapi.sse import ServerSentEvent
from collections.abc import AsyncIterable, Iterable
import json
from tech_doc_agent.app.api.schemas import (
    ChatRequest,
    ApproveRequest,
    HistoryViewResponse,
    SessionStateResponse,
)
from tech_doc_agent.app.core.guardrails import InputRisk, record_input_risk
from tech_doc_agent.app.core.observability import (
    get_trace_context,
    log_event,
    new_trace_id,
    trace_context,
)
from tech_doc_agent.app.core.tenant import TenantContext, tenant_from_values


router = APIRouter()
AGENT_NODE_NAMES = {
    "primary",
    "primary_assistant",
    "parser",
    "relation",
    "explanation",
    "examination",
    "summary",
}
TRANSITION_PREFIXES = (
    ("enter_", "enter"),
    ("finish_", "finish"),
    ("leave_", "leave"),
)

def get_runtime(request: Request) -> ChatRuntime:
    return request.app.state.runtime

def sse_event(event: str, data: dict) -> ServerSentEvent:
    payload = dict(data)
    context = get_trace_context()
    for key in ("trace_id", "session_id", "user_id", "namespace"):
        if context.get(key) and key not in payload:
            payload[key] = context[key]

    return ServerSentEvent(
        event=event,
        data=payload,
    )

def resolve_trace_id(body_trace_id: str | None, request: Request) -> str:
    return body_trace_id or request.headers.get("x-trace-id") or new_trace_id()

def resolve_tenant(
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
) -> TenantContext:
    return tenant_from_values(
        user_id or request.headers.get("x-user-id"),
        namespace or request.headers.get("x-namespace"),
    )

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

def stream_guardrail_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    risk: InputRisk,
    *,
    source: str,
    user_id: str | None = None,
    namespace: str | None = None,
) -> Iterable[ServerSentEvent]:
    snapshot = runtime.get_session_state(session_id, user_id=user_id, namespace=namespace)
    yield sse_event("session_snapshot", snapshot)
    yield _guardrail_interrupt_event(risk, session_id=session_id, source=source)

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

def _append_sse_field(lines: list[str], field: str, value: object) -> None:
    for line in str(value).splitlines() or [""]:
        lines.append(f"{field}: {line}\n")

def _encode_sse_event(event: ServerSentEvent) -> bytes:
    lines: list[str] = []

    if event.comment is not None:
        for line in str(event.comment).splitlines() or [""]:
            lines.append(f": {line}\n")
    if event.id is not None:
        _append_sse_field(lines, "id", event.id)
    if event.event is not None:
        _append_sse_field(lines, "event", event.event)
    if event.retry is not None:
        _append_sse_field(lines, "retry", event.retry)

    if event.raw_data is not None:
        data_str = event.raw_data
    elif event.data is not None:
        if hasattr(event.data, "model_dump_json"):
            data_str = event.data.model_dump_json()
        else:
            data_str = json.dumps(jsonable_encoder(event.data), ensure_ascii=False)
    else:
        data_str = None

    if data_str is not None:
        _append_sse_field(lines, "data", data_str)

    lines.append("\n")
    return "".join(lines).encode("utf-8")

async def _encoded_sse_events(
    events: AsyncIterable[ServerSentEvent],
) -> AsyncIterable[bytes]:
    async for event in events:
        yield _encode_sse_event(event)

def _event_source_response(events: AsyncIterable[ServerSentEvent]) -> StreamingResponse:
    return StreamingResponse(
        _encoded_sse_events(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

def iter_with_trace_context(
    events: Iterable[ServerSentEvent],
    trace_id: str,
    session_id: str,
    operation: str,
    user_id: str | None = None,
    namespace: str | None = None,
) -> Iterable[ServerSentEvent]:
    iterator = iter(events)

    while True:
        with trace_context(
            trace_id=trace_id,
            session_id=session_id,
            user_id=user_id,
            namespace=namespace,
            operation=operation,
        ):
            try:
                event = next(iterator)
            except StopIteration:
                return

        yield event

async def aiter_with_trace_context(
    events: AsyncIterable[ServerSentEvent],
    trace_id: str,
    session_id: str,
    operation: str,
    user_id: str | None = None,
    namespace: str | None = None,
) -> AsyncIterable[ServerSentEvent]:
    iterator = aiter(events)

    while True:
        with trace_context(
            trace_id=trace_id,
            session_id=session_id,
            user_id=user_id,
            namespace=namespace,
            operation=operation,
        ):
            try:
                event = await anext(iterator)
            except StopAsyncIteration:
                return

        yield event

def infer_agent_from_metadata(metadata: dict) -> str | None:
    node_name = metadata.get("langgraph_node")
    if node_name:
        return node_name

    for key in ("langgraph_checkpoint_ns", "checkpoint_ns"):
        checkpoint_ns = metadata.get(key)
        if isinstance(checkpoint_ns, str) and checkpoint_ns:
            candidate = checkpoint_ns.split(":", 1)[0]
            if candidate in AGENT_NODE_NAMES:
                return candidate

    path = metadata.get("langgraph_path")
    if isinstance(path, list):
        for item in reversed(path):
            if isinstance(item, str) and item in AGENT_NODE_NAMES:
                return item
            if isinstance(item, (list, tuple)):
                for part in reversed(item):
                    if isinstance(part, str) and part in AGENT_NODE_NAMES:
                        return part

    return None

def extract_text_from_chunk(msg_chunk) -> str:
    content = getattr(msg_chunk, "content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(item.get("text", ""))
        return "".join(parts)

    return ""

def extract_text_from_content(content) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(item.get("text", ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "".join(parts)

    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)

    return str(content) if content is not None else ""

def _agent_transition_payload(node_name: str) -> dict | None:
    for prefix, phase in TRANSITION_PREFIXES:
        if not node_name.startswith(prefix):
            continue

        agent = node_name[len(prefix):]
        if agent not in AGENT_NODE_NAMES:
            return None

        return {
            "phase": phase,
            "agent": agent,
        }

    return None

def _plan_update_payload(node_name: str, node_update: dict) -> dict:
    if node_name != "store_plan" and not node_name.startswith("finish_"):
        return {}

    payload = {}
    if "workflow_plan" in node_update:
        payload["plan"] = node_update["workflow_plan"]
    if "plan_index" in node_update:
        payload["plan_index"] = node_update["plan_index"]
    if "learning_target" in node_update:
        payload["learning_target"] = node_update["learning_target"]

    return payload

def _structured_result_events(node_name: str, node_update: dict) -> Iterable[ServerSentEvent]:
    for result_key in ("parser_result", "relation_result"):
        result = node_update.get(result_key)
        if not isinstance(result, dict):
            continue

        yield sse_event(
            "structured_result",
            {
                "node": node_name,
                "result_key": result_key,
                "result": result,
                "parsed": bool(result.get("parsed")),
            },
        )

def _stream_part_type_and_data(part) -> tuple[str | None, object]:
    if isinstance(part, dict):
        return part.get("type"), part.get("data")

    if isinstance(part, (tuple, list)) and len(part) == 2:
        return part[0], part[1]

    return None, None

def _extract_update_data(part) -> dict:
    if isinstance(part, dict):
        update_data = part.get("data", part)
    elif isinstance(part, (tuple, list)) and len(part) == 2:
        update_data = part[1]
    else:
        update_data = {}

    return update_data if isinstance(update_data, dict) else {}

def _extract_message_part_data(part_data) -> tuple[object, dict] | None:
    if not isinstance(part_data, (tuple, list)) or len(part_data) != 2:
        return None

    msg_chunk, metadata = part_data
    return msg_chunk, metadata if isinstance(metadata, dict) else {}

def _error_message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__

def iter_update_events(part) -> Iterable[ServerSentEvent]:
    update_data = _extract_update_data(part)

    if not isinstance(update_data, dict):
        return

    for node_name, node_update in update_data.items():
        transition_payload = _agent_transition_payload(node_name)
        if transition_payload:
            yield sse_event("agent_transition", transition_payload)

        if not isinstance(node_update, dict):
            continue

        plan_payload = _plan_update_payload(node_name, node_update)
        if plan_payload:
            yield sse_event("plan_update", plan_payload)

        yield from _structured_result_events(node_name, node_update)

        messages = node_update.get("messages", [])
        for message in messages:
            raw_type = getattr(message, "type", None)
            message_agent = getattr(message, "name", None) or node_name

            if raw_type == "ai":
                content = extract_text_from_content(getattr(message, "content", ""))
                if content.strip():
                    yield sse_event(
                        "agent_message",
                        {
                            "agent": message_agent,
                            "node": node_name,
                            "message_id": getattr(message, "id", None),
                            "content": content,
                        },
                    )

                tool_calls = getattr(message, "tool_calls", []) or []
                for tool_call in tool_calls:
                    yield sse_event(
                        "tool_call",
                        {
                            "agent": message_agent,
                            "node": node_name,
                            "tool": tool_call.get("name"),
                            "args": tool_call.get("args", {}),
                            "tool_call_id": tool_call.get("id"),
                        },
                    )

            elif raw_type == "tool":
                yield sse_event(
                    "tool_result",
                    {
                        "agent": node_name,
                        "node": node_name,
                        "tool": getattr(message, "name", None),
                        "tool_call_id": getattr(message, "tool_call_id", None),
                        "content": extract_text_from_content(getattr(message, "content", "")),
                    },
                )

def stream_parts_as_sse(
    runtime: ChatRuntime,
    session_id: str,
    parts,
    *,
    user_id: str | None = None,
    namespace: str | None = None,
) -> Iterable[ServerSentEvent]:
    try:
        for part in parts:
            part_type, part_data = _stream_part_type_and_data(part)

            if part_type == "messages":
                message_part = _extract_message_part_data(part_data)
                if message_part is None:
                    continue
                msg_chunk, metadata = message_part
                raw_type = getattr(msg_chunk, "type", None)

                if raw_type != "AIMessageChunk":
                    continue

                text = extract_text_from_chunk(msg_chunk)

                if not text:
                    continue

                yield sse_event(
                    "token",
                    {
                        "text": text,
                        "agent": infer_agent_from_metadata(metadata),
                    },
                )

            elif part_type == "updates":
                yield from iter_update_events(part)

        if runtime.has_pending_interrupt(session_id, user_id=user_id, namespace=namespace):
            yield sse_event(
                "interrupt_required",
                {
                    "session_id": session_id,
                    "pending": True,
                },
            )
            return

        yield sse_event(
            "done",
            {
                "session_id": session_id,
            },
        )

    except Exception as e:
        message = _error_message(e)
        log_event(
            "sse.stream.error",
            session_id=session_id,
            error_type=type(e).__name__,
            error=message,
        )
        yield sse_event(
            "error",
            {
                "message": message,
                "session_id": session_id,
            },
        )


async def astream_parts_as_sse(
    runtime: ChatRuntime,
    session_id: str,
    parts,
    *,
    user_id: str | None = None,
    namespace: str | None = None,
) -> AsyncIterable[ServerSentEvent]:
    try:
        async for part in parts:
            part_type, part_data = _stream_part_type_and_data(part)

            if part_type == "messages":
                message_part = _extract_message_part_data(part_data)
                if message_part is None:
                    continue
                msg_chunk, metadata = message_part
                raw_type = getattr(msg_chunk, "type", None)

                if raw_type != "AIMessageChunk":
                    continue

                text = extract_text_from_chunk(msg_chunk)

                if not text:
                    continue

                yield sse_event(
                    "token",
                    {
                        "text": text,
                        "agent": infer_agent_from_metadata(metadata),
                    },
                )

            elif part_type == "updates":
                for event in iter_update_events(part):
                    yield event

        if await runtime.ahas_pending_interrupt(session_id, user_id=user_id, namespace=namespace):
            yield sse_event(
                "interrupt_required",
                {
                    "session_id": session_id,
                    "pending": True,
                },
            )
            return

        yield sse_event(
            "done",
            {
                "session_id": session_id,
            },
        )

    except Exception as e:
        message = _error_message(e)
        log_event(
            "sse.stream.error",
            session_id=session_id,
            error_type=type(e).__name__,
            error=message,
            async_runtime=True,
        )
        yield sse_event(
            "error",
            {
                "message": message,
                "session_id": session_id,
            },
        )


def stream_chat_events(
    runtime: ChatRuntime,
    session_id: str,
    message: str,
    user_id: str | None = None,
    namespace: str | None = None,
    guardrail_checked: bool = False,
) -> Iterable[ServerSentEvent]:
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
            yield from stream_guardrail_approval_events(
                runtime,
                session_id,
                risk,
                source="chat.message",
                user_id=user_id,
                namespace=namespace,
            )
            return

    snapshot = runtime.get_session_state(session_id, user_id=user_id, namespace=namespace)
    yield sse_event("session_snapshot", snapshot)

    parts = runtime.stream_user_message(session_id, message, user_id=user_id, namespace=namespace)
    yield from stream_parts_as_sse(runtime, session_id, parts, user_id=user_id, namespace=namespace)

async def astream_chat_events(
    runtime: ChatRuntime,
    session_id: str,
    message: str,
    user_id: str | None = None,
    namespace: str | None = None,
    guardrail_checked: bool = False,
) -> AsyncIterable[ServerSentEvent]:
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

    parts = runtime.astream_user_message(session_id, message, user_id=user_id, namespace=namespace)
    async for event in astream_parts_as_sse(runtime, session_id, parts, user_id=user_id, namespace=namespace):
        yield event

def stream_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    approved: bool,
    feedback: str = "",
    user_id: str | None = None,
    namespace: str | None = None,
    guardrail_checked: bool = False,
) -> Iterable[ServerSentEvent]:
    if feedback and not guardrail_checked:
        risk = _record_guardrail_decision(feedback, source="chat.approval.feedback")
        if risk.level == "high":
            yield _guardrail_blocked_event(risk, session_id=session_id, source="chat.approval.feedback")
            return

    snapshot = runtime.get_session_state(session_id, user_id=user_id, namespace=namespace)
    yield sse_event("session_snapshot", snapshot)

    if not runtime.has_pending_interrupt(session_id, user_id=user_id, namespace=namespace):
        log_event("chat.approval.no_pending_interrupt", approved=approved)
        yield sse_event(
            "no_pending_interrupt",
            {
                "session_id": session_id,
            },
        )
        return

    parts = runtime.stream_approval(session_id, approved, feedback, user_id=user_id, namespace=namespace)
    yield from stream_parts_as_sse(runtime, session_id, parts, user_id=user_id, namespace=namespace)


async def astream_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    approved: bool,
    feedback: str = "",
    user_id: str | None = None,
    namespace: str | None = None,
    guardrail_checked: bool = False,
) -> AsyncIterable[ServerSentEvent]:
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

    parts = runtime.astream_approval(session_id, approved, feedback, user_id=user_id, namespace=namespace)
    async for event in astream_parts_as_sse(runtime, session_id, parts, user_id=user_id, namespace=namespace):
        yield event


@router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    runtime = get_runtime(request)
    trace_id = resolve_trace_id(body.trace_id, request)
    tenant = resolve_tenant(request, body.user_id, body.namespace)
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
            return _event_source_response(
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

    return _event_source_response(
        aiter_with_trace_context(
            astream_chat_events(
                runtime,
                body.session_id,
                body.message,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
                guardrail_checked=True,
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
    runtime = get_runtime(request)
    trace_id = resolve_trace_id(body.trace_id, request)
    tenant = resolve_tenant(request, body.user_id, body.namespace)
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

    return _event_source_response(
        aiter_with_trace_context(
            astream_approval_events(
                runtime,
                body.session_id,
                body.approved,
                body.feedback,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
                guardrail_checked=True,
            ),
            trace_id,
            body.session_id,
            "approval",
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
    )

@router.get("/sessions/{session_id}/history", response_model=HistoryViewResponse)
def get_history(
    session_id: str,
    request: Request,
    include_tools: bool = False,
    user_id: str | None = None,
    namespace: str | None = None,
):
    runtime = get_runtime(request)
    tenant = resolve_tenant(request, user_id, namespace)
    history = runtime.get_history_view(
        session_id,
        include_tools=include_tools,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    return HistoryViewResponse(**history)

@router.get("/sessions/{session_id}/state", response_model=SessionStateResponse)
def get_session_state(
    session_id: str,
    request: Request,
    user_id: str | None = None,
    namespace: str | None = None,
):
    runtime = get_runtime(request)
    tenant = resolve_tenant(request, user_id, namespace)
    state = runtime.get_session_state(
        session_id,
        user_id=tenant.user_id,
        namespace=tenant.namespace,
    )
    return SessionStateResponse(**state)
