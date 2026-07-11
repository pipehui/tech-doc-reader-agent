from fastapi.sse import ServerSentEvent

from tech_doc_agent.app.core.observability import get_trace_context


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
