from fastapi import APIRouter, Request
from customer_support_chat.app.services.chat_runtime import ChatRuntime
from fastapi.sse import ServerSentEvent, EventSourceResponse
from collections.abc import Iterable
from customer_support_chat.app.api.schemas import ChatRequest, ApproveRequest

router = APIRouter()

def get_runtime(request: Request) -> ChatRuntime:
    return request.app.state.runtime

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

def stream_parts_as_sse(runtime: ChatRuntime, session_id: str, parts) -> Iterable[ServerSentEvent]:
    for part in parts:
        if part["type"] != "messages":
            continue

        msg_chunk, metadata = part["data"]
        text = extract_text_from_chunk(msg_chunk)

        if not text:
            continue

        yield ServerSentEvent(
            event="token",
            data={
                "text": text,
                "agent": metadata.get("langgraph_node"),
            },
        )

    if runtime.has_pending_interrupt(session_id):
        yield ServerSentEvent(
            event="interrupt_required",
            data={
                "session_id": session_id,
                "pending": True,
            },
        )
        return

    yield ServerSentEvent(
        event="done",
        data={
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
