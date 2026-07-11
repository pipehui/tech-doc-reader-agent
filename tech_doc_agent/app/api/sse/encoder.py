import json
from collections.abc import AsyncIterable

from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi.sse import ServerSentEvent


def _append_sse_field(lines: list[str], field: str, value: object) -> None:
    for line in str(value).splitlines() or [""]:
        lines.append(f"{field}: {line}\n")


def encode_sse_event(event: ServerSentEvent) -> bytes:
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
        yield encode_sse_event(event)


def event_source_response(events: AsyncIterable[ServerSentEvent]) -> StreamingResponse:
    return StreamingResponse(
        _encoded_sse_events(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
