from typing import Callable

from langchain_core.messages import ToolMessage
from customer_support_chat.app.core.state import State


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

def create_tool_node_with_fallback(tools: list):
    from langchain_core.runnables import RunnableLambda
    from langgraph.prebuilt import ToolNode

    return ToolNode(tools).with_fallbacks(
        [RunnableLambda(handle_tool_error)], exception_key="error"
    )
