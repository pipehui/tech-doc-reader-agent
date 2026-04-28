import json
from typing import Callable

from langchain_core.messages import ToolMessage
from tech_doc_agent.app.core.state import State


def create_entry_node(assistant_name: str, new_dialog_state: str) -> Callable:
    def entry_node(state: State) -> dict:
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)

        if tool_calls:
            tool_call_id = tool_calls[0]["id"]
            return {"messages": [
                ToolMessage(
                    content=(
                        f"You are now acting as the {assistant_name} in a multi-agent technical document learning workflow. "
                        "Review the conversation and continue the current step using the existing context and any prior intermediate results. "
                        "Follow your own role-specific instructions and use the available tools when needed. "
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
                (tc for tc in tool_calls if tc["name"] == "CompleteOrEscalate"),
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



def extract_last_message_text(state: State) -> str:
    last_message = state["messages"][-1]
    content = last_message.content

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    
    return str(content)

def create_finish_node(result_key: str | None = None) -> Callable:
    def finish_node(state: State) -> dict:
        update = {
            "dialog_state": "pop",
            "plan_index": state.get("plan_index", 0) + 1,
        }

        if result_key is not None:
            update[result_key] = extract_last_message_text(state)

        return update
    return finish_node

def store_plan(state: State) -> dict:
    tool_call = state["messages"][-1].tool_calls[0]
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
        "parser_result": "",
        "relation_result": "",
        "learning_target": args["learning_target"],
    }

def handle_tool_error(state) -> dict:
    error = state.get("error")
    tool_calls = state["messages"][-1].tool_calls
    return {
        "messages": [
            {
                "type": "tool",
                "content": f"Error: {repr(error)}\nPlease fix your mistakes.",
                "tool_call_id": tc["id"],
            }
            for tc in tool_calls
        ]
    }

def _normalize_tool_args(args) -> str:
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return repr(args)

def _tool_call_signature(tool_call: dict) -> tuple[str, str]:
    return (
        tool_call.get("name", ""),
        _normalize_tool_args(tool_call.get("args", {})),
    )

def _count_trailing_identical_tool_calls(messages: list, signature: tuple[str, str]) -> int:
    count = 0

    for message in reversed(messages):
        raw_type = getattr(message, "type", None)

        if raw_type == "tool":
            continue

        if raw_type != "ai":
            break

        tool_calls = getattr(message, "tool_calls", []) or []
        if len(tool_calls) != 1:
            break

        if _tool_call_signature(tool_calls[0]) != signature:
            break

        count += 1

    return count

def _count_step_tool_calls(
    messages: list,
    step_name: str,
    tool_names: set[str],
) -> int:
    count = 0
    seen_step = False

    for message in reversed(messages):
        raw_type = getattr(message, "type", None)

        if raw_type == "tool":
            continue

        if raw_type == "ai":
            message_step = getattr(message, "name", None)

            if message_step == step_name:
                seen_step = True
                tool_calls = getattr(message, "tool_calls", []) or []
                count += sum(1 for tool_call in tool_calls if tool_call.get("name") in tool_names)
                continue

            if seen_step:
                break

            continue

        if seen_step:
            break

    return count

def maybe_block_repeated_tool_calls(state: State, max_identical_repeats: int = 2) -> dict | None:
    messages = state.get("messages", [])
    if not messages:
        return None

    last_message = messages[-1]
    if getattr(last_message, "type", None) != "ai":
        return None

    tool_calls = getattr(last_message, "tool_calls", []) or []
    if len(tool_calls) != 1:
        return None

    tool_call = tool_calls[0]
    signature = _tool_call_signature(tool_call)
    repeat_count = _count_trailing_identical_tool_calls(messages, signature)

    if repeat_count <= max_identical_repeats:
        return None

    dialog_state = state.get("dialog_state", [])
    current_step = dialog_state[-1] if dialog_state else "current"
    tool_name = tool_call.get("name", "tool")

    return {
        "messages": [
            ToolMessage(
                tool_call_id=tool_call["id"],
                content=(
                    f"Blocked repeated identical tool call to '{tool_name}' in step '{current_step}'. "
                    f"The same request has already been made {repeat_count - 1} times in a row and its prior result is already in context. "
                    "Do not call the same tool again with the same arguments in this step. "
                    "Use the existing tool result to continue the task, produce your structured output, or call CompleteOrEscalate if you truly cannot proceed."
                ),
            )
        ]
    }

def maybe_block_parser_tool_budget(
    state: State,
    max_total_calls: int = 6,
) -> dict | None:
    messages = state.get("messages", [])
    if not messages:
        return None

    dialog_state = state.get("dialog_state", [])
    current_step = dialog_state[-1] if dialog_state else ""
    if current_step != "parser":
        return None

    last_message = messages[-1]
    if getattr(last_message, "type", None) != "ai":
        return None

    tool_calls = getattr(last_message, "tool_calls", []) or []
    if len(tool_calls) != 1:
        return None

    tool_call = tool_calls[0]
    tool_name = tool_call.get("name", "")
    guarded_tools = {"read_docs", "web_search"}
    if tool_name not in guarded_tools:
        return None

    total_calls = _count_step_tool_calls(messages, "parser", guarded_tools)
    if total_calls <= max_total_calls:
        return None

    return {
        "messages": [
            ToolMessage(
                tool_call_id=tool_call["id"],
                content=(
                    "Blocked parser retrieval budget overflow. "
                    f"In the current parser step, read_docs and web_search have already been called {total_calls - 1} times. "
                    f"The total budget for these retrieval tools is {max_total_calls}. "
                    "Do not continue searching. Use the existing retrieved material to finish the structured parsing result, "
                    "or call CompleteOrEscalate if the remaining uncertainty is too high."
                ),
            )
        ]
    }

def create_tool_node_with_fallback(tools: list):
    from langchain_core.runnables import RunnableLambda
    from langgraph.prebuilt import ToolNode

    tool_node = ToolNode(tools)

    def guarded_tool_node(state: State):
        blocked = maybe_block_parser_tool_budget(state)
        if blocked is not None:
            return blocked

        blocked = maybe_block_repeated_tool_calls(state)
        if blocked is not None:
            return blocked

        return tool_node.invoke(state)

    return RunnableLambda(guarded_tool_node).with_fallbacks(
        [RunnableLambda(handle_tool_error)], exception_key="error"
    )
