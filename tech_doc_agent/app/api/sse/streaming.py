from collections.abc import AsyncIterable, Iterable

from fastapi.sse import ServerSentEvent

from tech_doc_agent.app.core.errors import ApplicationError, classify_error, safe_error_fields
from tech_doc_agent.app.core.local_tracing import record_local_exception
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.runtime.chat_runtime import ChatRuntime

from .agent_metadata import infer_agent_from_metadata
from .events import sse_event
from .message_translator import extract_text_from_chunk
from .parts import (
    extract_message_part_data,
    stream_part_type_and_data,
)
from .update_translator import iter_update_events


def _stream_error(exc: BaseException) -> ApplicationError:
    mapped = classify_error(exc)
    if mapped.dependency is None:
        return mapped.with_context(dependency="agent_runtime")
    return mapped


def _stream_error_payload(exc: BaseException, session_id: str) -> dict:
    error = _stream_error(exc)
    return {
        "status": "error",
        "code": error.code,
        "retryable": error.retryable,
        "message": error.safe_message,
        "safe_message": error.safe_message,
        "dependency": error.dependency,
        "cause_type": error.cause_type,
        "session_id": session_id,
    }


def events_from_stream_part(part) -> Iterable[ServerSentEvent]:
    part_type, part_data = stream_part_type_and_data(part)

    if part_type == "messages":
        message_part = extract_message_part_data(part_data)
        if message_part is None:
            log_event("sse.translation.ignored", reason="malformed_message_part")
            return
        msg_chunk, metadata = message_part
        if getattr(msg_chunk, "type", None) != "AIMessageChunk":
            log_event(
                "sse.translation.ignored",
                reason="unsupported_message_chunk",
                chunk_type=type(msg_chunk).__name__,
            )
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
        return

    log_event(
        "sse.translation.ignored",
        reason="unsupported_stream_part",
        part_type=(
            part_type
            if isinstance(part_type, str) and part_type in {"messages", "updates"}
            else "unknown"
        ),
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
        record_local_exception(exc, name="sse.stream.translation")
        error = _stream_error(exc)
        log_event(
            "sse.stream.error",
            session_id=session_id,
            **safe_error_fields(error),
        )
        yield sse_event(
            "error",
            _stream_error_payload(error, session_id),
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
        record_local_exception(exc, name="sse.stream.translation")
        error = _stream_error(exc)
        log_event(
            "sse.stream.error",
            session_id=session_id,
            async_runtime=True,
            **safe_error_fields(error),
        )
        yield sse_event(
            "error",
            _stream_error_payload(error, session_id),
        )
