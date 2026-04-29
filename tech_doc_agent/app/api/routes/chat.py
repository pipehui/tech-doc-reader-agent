from fastapi import APIRouter, Request
from tech_doc_agent.app.services.chat_runtime import ChatRuntime
from fastapi.sse import ServerSentEvent, EventSourceResponse
from collections.abc import Iterable
import json
from tech_doc_agent.app.api.schemas import (
    ChatRequest,
    ApproveRequest,
    HistoryViewResponse,
    SessionStateResponse,
)
from tech_doc_agent.app.core.guardrails import record_input_risk
from tech_doc_agent.app.core.observability import (
    get_trace_context,
    log_event,
    new_trace_id,
    trace_context,
)


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
    for key in ("trace_id", "session_id"):
        if context.get(key) and key not in payload:
            payload[key] = context[key]

    return ServerSentEvent(
        event=event,
        data=payload,
    )

def resolve_trace_id(body_trace_id: str | None, request: Request) -> str:
    return body_trace_id or request.headers.get("x-trace-id") or new_trace_id()

def iter_with_trace_context(
    events: Iterable[ServerSentEvent],
    trace_id: str,
    session_id: str,
    operation: str,
) -> Iterable[ServerSentEvent]:
    iterator = iter(events)

    while True:
        with trace_context(trace_id=trace_id, session_id=session_id, operation=operation):
            try:
                event = next(iterator)
            except StopIteration:
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

def stream_parts_as_sse(runtime: ChatRuntime, session_id: str, parts) -> Iterable[ServerSentEvent]:
    try:
        for part in parts:
            part_type, part_data = _stream_part_type_and_data(part)

            if part_type == "messages":
                msg_chunk, metadata = part_data
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

        if runtime.has_pending_interrupt(session_id):
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
        log_event(
            "sse.stream.error",
            session_id=session_id,
            error_type=type(e).__name__,
            error=str(e),
        )
        yield sse_event(
            "error",
            {
                "message": str(e),
                "session_id": session_id,
            },
        )


def stream_chat_events(
    runtime: ChatRuntime,
    session_id: str,
    message: str,
) -> Iterable[ServerSentEvent]:
    record_input_risk(message, source="chat.message")
    snapshot = runtime.get_session_state(session_id)
    yield sse_event("session_snapshot", snapshot)

    parts = runtime.stream_user_message(session_id, message)
    yield from stream_parts_as_sse(runtime, session_id, parts)

def stream_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    approved: bool,
    feedback: str = "",
) -> Iterable[ServerSentEvent]:
    if feedback:
        record_input_risk(feedback, source="chat.approval.feedback")

    snapshot = runtime.get_session_state(session_id)
    yield sse_event("session_snapshot", snapshot)

    if not runtime.has_pending_interrupt(session_id):
        log_event("chat.approval.no_pending_interrupt", approved=approved)
        yield sse_event(
            "no_pending_interrupt",
            {
                "session_id": session_id,
            },
        )
        return

    parts = runtime.stream_approval(session_id, approved, feedback)
    yield from stream_parts_as_sse(runtime, session_id, parts)


@router.post("/chat", response_class=EventSourceResponse)
def chat(body: ChatRequest, request: Request):
    runtime = get_runtime(request)
    trace_id = resolve_trace_id(body.trace_id, request)
    return iter_with_trace_context(
        stream_chat_events(runtime, body.session_id, body.message),
        trace_id,
        body.session_id,
        "chat",
    )

@router.post("/chat/approve", response_class=EventSourceResponse)
def approve(body: ApproveRequest, request: Request):
    runtime = get_runtime(request)
    trace_id = resolve_trace_id(body.trace_id, request)
    return iter_with_trace_context(
        stream_approval_events(runtime, body.session_id, body.approved, body.feedback),
        trace_id,
        body.session_id,
        "approval",
    )

@router.get("/sessions/{session_id}/history", response_model=HistoryViewResponse)
def get_history(
    session_id: str,
    request: Request,
    include_tools: bool = False,
):
    runtime = get_runtime(request)
    history = runtime.get_history_view(
        session_id,
        include_tools=include_tools,
    )
    return HistoryViewResponse(**history)

@router.get("/sessions/{session_id}/state", response_model=SessionStateResponse)
def get_session_state(session_id: str, request: Request):
    runtime = get_runtime(request)
    state = runtime.get_session_state(session_id)
    return SessionStateResponse(**state)
