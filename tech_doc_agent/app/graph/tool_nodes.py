from __future__ import annotations

from time import perf_counter

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.prebuilt import ToolNode

from tech_doc_agent.app.core.errors import classify_error, safe_error_fields
from tech_doc_agent.app.core.observability import log_event, timed_node

from .specs import ToolExecutionPolicy
from .state import State
from .tool_policy import evaluate_tool_policy


TOOL_DEPENDENCIES = {
    "web_search": "web_search",
    "read_docs": "document_repository",
    "save_docs": "document_repository",
    "search_related_docs": "semantic_search",
    "read_learning_history": "learning_repository",
    "read_all_learning_history": "learning_repository",
    "upsert_learning_history": "learning_state_repository",
    "read_user_memory": "memory_repository",
    "upsert_learning_state": "learning_state_repository",
}


def _tool_dependency(tool_name: str | None) -> str | None:
    return TOOL_DEPENDENCIES.get(tool_name or "")


def handle_tool_error(state) -> dict:
    raw_error = state.get("error")
    error = raw_error if isinstance(raw_error, BaseException) else RuntimeError("Tool execution failed.")
    tool_calls = getattr(state["messages"][-1], "tool_calls", [])

    messages = []
    for tool_call in tool_calls:
        tool_name = tool_call.get("name")
        mapped = classify_error(
            error,
            dependency=_tool_dependency(tool_name),
            tool=tool_name,
        )
        payload = mapped.to_payload()
        messages.append(
            ToolMessage(
                name=tool_name,
                content=mapped.to_json(),
                artifact={"error": payload},
                tool_call_id=tool_call["id"],
                status="error",
            )
        )

    return {
        "messages": messages,
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


def _log_tool_errors(
    state: State,
    tool_calls: list[dict],
    exc: BaseException,
    *,
    elapsed_ms: float,
    async_runtime: bool = False,
) -> None:
    for tool_call in tool_calls:
        tool_name = tool_call.get("name")
        error_fields = safe_error_fields(
            exc,
            dependency=_tool_dependency(tool_name),
            tool=tool_name,
        )
        error_fields.pop("tool")
        _log_tool_calls(
            "tool_call.error",
            state,
            [tool_call],
            elapsed_ms=elapsed_ms,
            async_runtime=async_runtime,
            **error_fields,
        )


def _blocked_tool_call_update(state: State, policy: ToolExecutionPolicy) -> dict | None:
    decision = evaluate_tool_policy(state, policy)
    if not decision.is_blocked:
        return None

    _log_tool_calls(
        "tool_call.blocked",
        state,
        _pending_tool_calls(state),
        policy_action=decision.action,
        reason=decision.reason,
        observed_calls=decision.observed_calls,
        configured_limit=decision.limit,
    )
    return decision.to_graph_update()


def create_tool_node_with_fallback(tools: list, policy: ToolExecutionPolicy):
    tool_node = ToolNode(tools, handle_tool_errors=False)

    def guarded_tool_node(state: State):
        blocked = _blocked_tool_call_update(state, policy)
        if blocked is not None:
            return blocked

        tool_calls = _pending_tool_calls(state)
        start = perf_counter()

        try:
            with timed_node("tool_node", agent_node=_current_step(state), tool_count=len(tool_calls)):
                result = tool_node.invoke(state)
        except Exception as exc:
            _log_tool_errors(
                state,
                tool_calls,
                exc,
                elapsed_ms=_elapsed_ms(start),
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
        blocked = _blocked_tool_call_update(state, policy)
        if blocked is not None:
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
            _log_tool_errors(
                state,
                tool_calls,
                exc,
                elapsed_ms=_elapsed_ms(start),
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
