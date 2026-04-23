from fastapi import APIRouter, Request
from customer_support_chat.app.services.chat_runtime import ChatRuntime
from fastapi.sse import ServerSentEvent, EventSourceResponse
from collections.abc import Iterable
import json
from customer_support_chat.app.api.schemas import (
    ChatRequest,
    ApproveRequest,
    HistoryViewResponse,
    SessionStateResponse,
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

def get_runtime(request: Request) -> ChatRuntime:
    return request.app.state.runtime

def sse_event(event: str, data: dict) -> ServerSentEvent:
    return ServerSentEvent(
        event=event,
        data=data,
    )

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

def iter_update_events(part) -> Iterable[ServerSentEvent]:
    update_data = part.get("data", {})

    if not isinstance(update_data, dict):
        return

    for node_name, node_update in update_data.items():
        if not isinstance(node_update, dict):
            continue

        messages = node_update.get("messages", [])
        for message in messages:
            raw_type = getattr(message, "type", None)

            if raw_type == "ai":
                content = extract_text_from_content(getattr(message, "content", ""))
                if content.strip():
                    yield sse_event(
                        "agent_message",
                        {
                            "agent": node_name,
                            "message_id": getattr(message, "id", None),
                            "content": content,
                        },
                    )

                tool_calls = getattr(message, "tool_calls", []) or []
                for tool_call in tool_calls:
                    yield sse_event(
                        "tool_call",
                        {
                            "agent": node_name,
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
                        "tool": getattr(message, "name", None),
                        "tool_call_id": getattr(message, "tool_call_id", None),
                        "content": extract_text_from_content(getattr(message, "content", "")),
                    },
                )

def stream_parts_as_sse(runtime: ChatRuntime, session_id: str, parts) -> Iterable[ServerSentEvent]:
    try:
        for part in parts:
            part_type = part.get("type")

            if part_type == "messages":
                msg_chunk, metadata = part["data"]
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
        yield sse_event(
            "error",
            {
                "message": str(e),
                "session_id": session_id,
            },
        )


def stream_chat_events(runtime: ChatRuntime, session_id: str, message: str) -> Iterable[ServerSentEvent]:
    parts = runtime.stream_user_message(session_id, message)
    yield from stream_parts_as_sse(runtime, session_id, parts)

def stream_approval_events(
    runtime: ChatRuntime,
    session_id: str,
    approved: bool,
    feedback: str = "",
) -> Iterable[ServerSentEvent]:
    if not runtime.has_pending_interrupt(session_id):
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
    return stream_chat_events(runtime, body.session_id, body.message)

@router.post("/chat/approve", response_class=EventSourceResponse)
def approve(body: ApproveRequest, request: Request):
    runtime = get_runtime(request)
    return stream_approval_events(
        runtime,
        body.session_id,
        body.approved,
        body.feedback,
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
