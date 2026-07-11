from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from .state import State


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
                status="error",
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
                status="error",
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
