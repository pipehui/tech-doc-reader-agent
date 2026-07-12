from collections.abc import Mapping
from typing import Any

from fastapi.sse import ServerSentEvent

from tech_doc_agent.app.core.observability import get_trace_context

from .contract import SseEventName
from .payloads import validate_sse_payload


def sse_event(event: SseEventName, data: Mapping[str, Any]) -> ServerSentEvent:
    payload = dict(data)
    context = get_trace_context()
    for key in ("trace_id", "session_id", "user_id", "namespace"):
        if context.get(key) and key not in payload:
            payload[key] = context[key]

    return ServerSentEvent(
        event=event,
        data=validate_sse_payload(event, payload),
    )
