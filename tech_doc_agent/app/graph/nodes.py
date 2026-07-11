from collections.abc import Callable

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.structured_outputs import ResultKind, parse_structured_result
from tech_doc_agent.app.core.tenant import tenant_from_values
from tech_doc_agent.app.services.message_scope import build_scoped_state

from .state import State
from .messages import extract_last_message_text


def assistant_node(assistant, scoped_messages: bool = False):
    def invoke(state: State, config: RunnableConfig | None = None):
        assistant_state = build_scoped_state(state, assistant.name) if scoped_messages else state
        return assistant(assistant_state, config)

    async def ainvoke(state: State, config: RunnableConfig | None = None):
        assistant_state = build_scoped_state(state, assistant.name) if scoped_messages else state
        return await assistant.ainvoke(assistant_state, config)

    return RunnableLambda(invoke, afunc=ainvoke, name=assistant.name)


def create_user_info_node(context_provider: Callable[..., str]) -> Callable:
    def user_info(state: State, config: RunnableConfig):
        metadata = (config or {}).get("metadata", {}) if isinstance(config, dict) else {}
        tenant = tenant_from_values(
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
            }

        return {
            "dialog_state": new_dialog_state,
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
    }
