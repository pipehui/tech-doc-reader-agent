from __future__ import annotations

from time import perf_counter

from langchain_core.runnables import RunnableLambda
from langgraph.prebuilt import ToolNode

from tech_doc_agent.app.core.observability import log_event, timed_node

from .state import State
from .tool_policy import maybe_block_parser_tool_budget, maybe_block_repeated_tool_calls


def handle_tool_error(state) -> dict:
    error = state.get("error")
    tool_calls = getattr(state["messages"][-1], "tool_calls", [])
    return {
        "messages": [
            {
                "type": "tool",
                "content": f"Error: {repr(error)}\nPlease fix your mistakes.",
                "tool_call_id": tool_call["id"],
                "status": "error",
            }
            for tool_call in tool_calls
        ]
    }


def _elapsed_ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 2)


def _current_step(state: State) -> str:
    dialog_state = state.get("dialog_state", [])
    return dialog_state[-1] if dialog_state else "primary"


def _pending_tool_calls(state: State) -> list[dict]:
    messages = state.get("messages", [])
    if not messages:
        return []

    return list(getattr(messages[-1], "tool_calls", []) or [])


def _log_tool_calls(
    event: str,
    state: State,
    tool_calls: list[dict],
    **fields,
) -> None:
    current_step = _current_step(state)
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    agent = getattr(last_message, "name", None) or current_step

    for tool_call in tool_calls:
        log_event(
            event,
            agent=agent,
            node=current_step,
            tool=tool_call.get("name"),
            tool_call_id=tool_call.get("id"),
            **fields,
        )


def create_tool_node_with_fallback(tools: list):
    tool_node = ToolNode(tools)

    def guarded_tool_node(state: State):
        blocked = maybe_block_parser_tool_budget(state)
        if blocked is not None:
            _log_tool_calls(
                "tool_call.blocked",
                state,
                _pending_tool_calls(state),
                reason="parser_tool_budget",
            )
            return blocked

        blocked = maybe_block_repeated_tool_calls(state)
        if blocked is not None:
            _log_tool_calls(
                "tool_call.blocked",
                state,
                _pending_tool_calls(state),
                reason="repeated_tool_call",
            )
            return blocked

        tool_calls = _pending_tool_calls(state)
        start = perf_counter()

        try:
            with timed_node("tool_node", agent_node=_current_step(state), tool_count=len(tool_calls)):
                result = tool_node.invoke(state)
        except Exception as exc:
            _log_tool_calls(
                "tool_call.error",
                state,
                tool_calls,
                elapsed_ms=_elapsed_ms(start),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

        _log_tool_calls(
            "tool_call.finished",
            state,
            tool_calls,
            elapsed_ms=_elapsed_ms(start),
            success=True,
        )
        return result

    async def aguarded_tool_node(state: State):
        blocked = maybe_block_parser_tool_budget(state)
        if blocked is not None:
            _log_tool_calls(
                "tool_call.blocked",
                state,
                _pending_tool_calls(state),
                reason="parser_tool_budget",
            )
            return blocked

        blocked = maybe_block_repeated_tool_calls(state)
        if blocked is not None:
            _log_tool_calls(
                "tool_call.blocked",
                state,
                _pending_tool_calls(state),
                reason="repeated_tool_call",
            )
            return blocked

        tool_calls = _pending_tool_calls(state)
        start = perf_counter()

        try:
            with timed_node(
                "tool_node",
                agent_node=_current_step(state),
                tool_count=len(tool_calls),
                async_runtime=True,
            ):
                result = await tool_node.ainvoke(state)
        except Exception as exc:
            _log_tool_calls(
                "tool_call.error",
                state,
                tool_calls,
                elapsed_ms=_elapsed_ms(start),
                error_type=type(exc).__name__,
                error=str(exc),
                async_runtime=True,
            )
            raise

        _log_tool_calls(
            "tool_call.finished",
            state,
            tool_calls,
            elapsed_ms=_elapsed_ms(start),
            success=True,
            async_runtime=True,
        )
        return result

    return RunnableLambda(guarded_tool_node, afunc=aguarded_tool_node).with_fallbacks(
        [RunnableLambda(handle_tool_error)], exception_key="error"
    )
