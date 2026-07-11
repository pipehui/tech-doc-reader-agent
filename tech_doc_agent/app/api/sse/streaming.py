from collections.abc import AsyncIterable, Iterable

from fastapi.sse import ServerSentEvent

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.services.chat_runtime import ChatRuntime

from .events import sse_event
from .translators import (
    extract_message_part_data,
    extract_text_from_chunk,
    infer_agent_from_metadata,
    iter_update_events,
    stream_part_type_and_data,
)


def _error_message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def events_from_stream_part(part) -> Iterable[ServerSentEvent]:
    part_type, part_data = stream_part_type_and_data(part)

    if part_type == "messages":
        message_part = extract_message_part_data(part_data)
        if message_part is None:
            return
        msg_chunk, metadata = message_part
        if getattr(msg_chunk, "type", None) != "AIMessageChunk":
            return

        text = extract_text_from_chunk(msg_chunk)
        if text:
            yield sse_event(
                "token",
                {
                    "text": text,
                    "agent": infer_agent_from_metadata(metadata),
                },
            )
        return

    if part_type == "updates":
        yield from iter_update_events(part)


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
            yield from events_from_stream_part(part)

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

    except Exception as exc:
        message = _error_message(exc)
        log_event(
            "sse.stream.error",
            session_id=session_id,
            error_type=type(exc).__name__,
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
            for event in events_from_stream_part(part):
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

    except Exception as exc:
        message = _error_message(exc)
        log_event(
            "sse.stream.error",
            session_id=session_id,
            error_type=type(exc).__name__,
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
