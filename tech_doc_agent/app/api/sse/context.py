from collections.abc import AsyncIterable, Iterable

from fastapi.sse import ServerSentEvent

from tech_doc_agent.app.core.observability import trace_context


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
