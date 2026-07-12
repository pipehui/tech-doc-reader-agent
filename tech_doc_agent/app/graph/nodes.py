from collections.abc import Callable

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda

from tech_doc_agent.app.core.errors import Conflict
from tech_doc_agent.app.core.execution_budget import ExecutionBudgetExceeded
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.structured_outputs import ResultKind, parse_structured_result
from tech_doc_agent.app.core.tenant import parse_tenant
from tech_doc_agent.app.services.message_scope import build_scoped_state

from .state import State
from .budgeting import WorkflowBudgetTracker
from .messages import extract_last_message_text
from .reflection import reflection_active_reset, reflection_request_reset


def assistant_node(
    assistant,
    scoped_messages: bool = False,
    budget_tracker: WorkflowBudgetTracker | None = None,
):
    def invoke(state: State, config: RunnableConfig | None = None):
        assistant_state = build_scoped_state(state, assistant.name) if scoped_messages else state
        current_usage = budget_tracker.current(state) if budget_tracker is not None else None
        before_llm_attempt = None
        if budget_tracker is not None and current_usage is not None:
            before_llm_attempt = lambda local_usages: budget_tracker.assert_before_llm_attempt(
                state,
                config,
                current=current_usage,
                local_usages=local_usages,
            )
        try:
            update = assistant(
                assistant_state,
                config,
                before_llm_attempt=before_llm_attempt,
            )
        except ExecutionBudgetExceeded as exc:
            update = {
                "_llm_usage": (),
                "_budget_decision": exc.decision,
            }
        if budget_tracker is not None:
            update = budget_tracker.record_assistant(
                state,
                update,
                config=config,
                current=current_usage,
            )
        else:
            update.pop("_llm_usage", None)
            update.pop("_budget_decision", None)
        return _complete_reflection_state(state, update)

    async def ainvoke(state: State, config: RunnableConfig | None = None):
        assistant_state = build_scoped_state(state, assistant.name) if scoped_messages else state
        current_usage = budget_tracker.current(state) if budget_tracker is not None else None
        before_llm_attempt = None
        if budget_tracker is not None and current_usage is not None:
            before_llm_attempt = lambda local_usages: budget_tracker.assert_before_llm_attempt(
                state,
                config,
                current=current_usage,
                local_usages=local_usages,
            )
        try:
            result = await assistant.ainvoke(
                assistant_state,
                config,
                before_llm_attempt=before_llm_attempt,
            )
        except ExecutionBudgetExceeded as exc:
            result = {
                "_llm_usage": (),
                "_budget_decision": exc.decision,
            }
        if budget_tracker is not None:
            result = budget_tracker.record_assistant(
                state,
                result,
                config=config,
                current=current_usage,
            )
        else:
            result.pop("_llm_usage", None)
            result.pop("_budget_decision", None)
        return _complete_reflection_state(state, result)

    return RunnableLambda(invoke, afunc=ainvoke, name=assistant.name)


def _complete_reflection_state(state: State, assistant_update: dict) -> dict:
    if state.get("reflection_status") not in {"repairing", "finalizing", "terminal"}:
        return assistant_update

    result = assistant_update.get("messages")
    last_message = result[-1] if isinstance(result, list) and result else result
    if getattr(last_message, "tool_calls", None):
        return assistant_update
    return {
        **assistant_update,
        **reflection_active_reset(),
    }


def create_user_info_node(context_provider: Callable[..., str]) -> Callable:
    def user_info(state: State, config: RunnableConfig):
        metadata = (config or {}).get("metadata", {}) if isinstance(config, dict) else {}
        tenant = parse_tenant(
            state.get("user_id") or metadata.get("user_id"),
            state.get("namespace") or metadata.get("namespace"),
        )
        info_str = context_provider(
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            memory_query=state.get("learning_target", ""),
        )
        update = {
            "user_info": info_str,
            "user_id": tenant.user_id,
            "namespace": tenant.namespace,
            "learning_target": state.get("learning_target", ""),
            **reflection_request_reset(),
        }

        if state.get("examination_context") and not _last_ai_was_examination(state):
            update["examination_context"] = ""

        return update

    return user_info


def _last_ai_was_examination(state: State) -> bool:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", None) == "ai":
            return getattr(message, "name", None) == "examination"
    return False


def create_entry_node(assistant_name: str, new_dialog_state: str) -> Callable:
    def entry_node(state: State) -> dict:
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)

        if tool_calls:
            tool_call_id = tool_calls[0]["id"]
            return {
                "messages": [
                    ToolMessage(
                        content=(
                            f"You are now acting as the {assistant_name} in a multi-agent technical document learning workflow. "
                            "Use the current task brief, structured state fields, and your own tool results for this step. "
                            "Follow your own role-specific instructions and use the available tools when needed. "
                            "Do not rely on hidden primary messages or other agents' raw tool results unless they are included as structured state. "
                            "Do not mention internal routing, workflow planning, or handoff details to the user. "
                            "If the task has changed, the current step is no longer appropriate, or you cannot continue safely, "
                            "call CompleteOrEscalate so the primary assistant can take over."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "dialog_state": new_dialog_state,
                **reflection_active_reset(),
            }

        return {
            "dialog_state": new_dialog_state,
            **reflection_active_reset(),
        }

    return entry_node


def create_exit_node() -> Callable:
    def exit_node(state: State) -> dict:
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)

        base_update = {
            "dialog_state": "pop",
            "workflow_plan": [],
            "plan_index": 0,
            **reflection_active_reset(),
        }

        if tool_calls:
            handoff_call = next(
                (tool_call for tool_call in tool_calls if tool_call["name"] == "CompleteOrEscalate"),
                None,
            )
            if handoff_call:
                return {
                    "messages": [
                        ToolMessage(
                            content="Current step ended early. Control is returned to the primary assistant.",
                            tool_call_id=handoff_call["id"],
                        )
                    ],
                    **base_update,
                }

            if state.get("reflection_status") == "finalizing":
                messages = _reflection_closed_tool_messages(state)
                log_event(
                    "reflection.terminated",
                    agent=(state.get("dialog_state", []) or ["subagent"])[-1],
                    tool=messages[0].name if messages else "tool",
                    error_code="reflection_tool_chain_closed",
                    reflection_rounds_used=state.get("reflection_rounds_used", 0),
                    reason="finalization_tool_call_blocked",
                    error_count=len(messages),
                )
                return {
                    "messages": messages,
                    **base_update,
                }

        return base_update

    return exit_node


def create_finish_node(
    result_key: str | None = None,
    structured_kind: ResultKind | None = None,
) -> Callable:
    def finish_node(state: State) -> dict:
        update = {
            "dialog_state": "pop",
            "plan_index": state.get("plan_index", 0) + 1,
            **reflection_active_reset(),
        }

        if result_key is not None:
            raw_text = extract_last_message_text(state)
            if structured_kind is not None:
                result = parse_structured_result(structured_kind, raw_text)
                log_event(
                    "assistant.structured_result",
                    result_key=result_key,
                    result_kind=structured_kind,
                    parsed=result.get("parsed", False),
                )
                update[result_key] = result
            else:
                update[result_key] = raw_text

        return update

    return finish_node


def create_primary_tool_failure_node() -> Callable:
    def primary_tool_failure(state: State) -> dict:
        closed_messages = _reflection_closed_tool_messages(state)
        reason = (
            "finalization_tool_call_blocked"
            if closed_messages
            else state.get("reflection_terminal_reason", "non_repairable_error")
        )
        if reason == "max_rounds_exhausted":
            content = (
                "工具参数在一次受控修正后仍未通过校验。为避免重复调用，本次已停止该工具链。"
                "你可以调整请求后再试。"
            )
        else:
            content = (
                "该工具错误无法通过修改参数安全恢复。为避免重复调用，本次已停止该工具链。"
                "请稍后重试或调整请求。"
            )
        log_event(
            "reflection.terminated",
            agent="primary",
            tool=state.get("reflection_tool", "tool"),
            error_code=state.get("reflection_error_code", "tool_error"),
            reflection_rounds_used=state.get("reflection_rounds_used", 0),
            reason=reason,
        )
        return {
            "messages": [*closed_messages, AIMessage(content=content, name="primary")],
            "workflow_plan": [],
            "plan_index": 0,
            **reflection_active_reset(),
        }

    return primary_tool_failure


def _reflection_closed_tool_messages(state: State) -> list[ToolMessage]:
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    tool_calls = list(getattr(last_message, "tool_calls", []) or [])
    if state.get("reflection_status") != "finalizing" or not tool_calls:
        return []

    results = []
    for tool_call in tool_calls:
        tool_name = tool_call.get("name") or "tool"
        error = Conflict(
            "The reflection tool chain is closed. Continue without another tool call.",
            code="reflection_tool_chain_closed",
            tool=tool_name,
            cause_type="ReflectionPolicy",
        )
        results.append(
            ToolMessage(
                name=tool_name,
                tool_call_id=tool_call["id"],
                status="error",
                content=error.to_json(),
                artifact={"error": error.to_payload()},
            )
        )
    return results


def store_plan(state: State) -> dict:
    tool_call = getattr(state["messages"][-1], "tool_calls", [])[0]
    args = tool_call["args"]

    return {
        "messages": [
            ToolMessage(
                tool_call_id=tool_call["id"],
                content=f"Workflow plan stored: {args['steps']}",
            )
        ],
        "workflow_plan": args["steps"],
        "plan_index": 0,
        "parser_result": {},
        "relation_result": {},
        "learning_target": args["learning_target"],
        **reflection_active_reset(),
    }
