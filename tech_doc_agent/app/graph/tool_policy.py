from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import ToolMessage

from tech_doc_agent.app.core.errors import Conflict

from .specs import ToolExecutionPolicy
from .state import State


ToolPolicyAction = Literal["allow", "block"]
ToolPolicyReason = Literal["parser_tool_budget", "repeated_tool_call"]


@dataclass(frozen=True)
class ToolPolicyDecision:
    action: ToolPolicyAction
    reason: ToolPolicyReason | None = None
    tool_name: str | None = None
    observed_calls: int | None = None
    limit: int | None = None
    message: ToolMessage | None = None

    def __post_init__(self) -> None:
        if self.action == "allow":
            if any(
                value is not None
                for value in (
                    self.reason,
                    self.tool_name,
                    self.observed_calls,
                    self.limit,
                    self.message,
                )
            ):
                raise ValueError("Allow decisions cannot contain block metadata.")
            return

        if self.action != "block":
            raise ValueError(f"Unsupported tool policy action: {self.action}")
        if self.reason is None or self.message is None:
            raise ValueError("Block decisions require a reason and ToolMessage.")
        if self.observed_calls is None or self.limit is None:
            raise ValueError("Block decisions require observed_calls and limit.")

    @classmethod
    def allow(cls) -> ToolPolicyDecision:
        return cls(action="allow")

    @classmethod
    def block(
        cls,
        *,
        reason: ToolPolicyReason,
        tool_name: str,
        observed_calls: int,
        limit: int,
        message: ToolMessage,
    ) -> ToolPolicyDecision:
        return cls(
            action="block",
            reason=reason,
            tool_name=tool_name,
            observed_calls=observed_calls,
            limit=limit,
            message=message,
        )

    @property
    def is_blocked(self) -> bool:
        return self.action == "block"

    def to_graph_update(self) -> dict[str, list[ToolMessage]]:
        if not self.is_blocked or self.message is None:
            raise ValueError("Only block decisions can be converted to a graph update.")
        return {"messages": [self.message]}


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


def _blocked_decision(
    *,
    tool_call: dict,
    reason: ToolPolicyReason,
    observed_calls: int,
    limit: int,
    content: str,
    error_code: str,
) -> ToolPolicyDecision:
    tool_name = tool_call.get("name") or "tool"
    error = Conflict(
        content,
        code=error_code,
        tool=tool_name,
        cause_type="ToolPolicy",
    )
    return ToolPolicyDecision.block(
        reason=reason,
        tool_name=tool_name,
        observed_calls=observed_calls,
        limit=limit,
        message=ToolMessage(
            tool_call_id=tool_call["id"],
            status="error",
            content=content,
            artifact={"error": error.to_payload()},
        ),
    )


def evaluate_repeated_tool_calls(
    state: State,
    *,
    max_identical_repeats: int,
) -> ToolPolicyDecision:
    messages = state.get("messages", [])
    if not messages:
        return ToolPolicyDecision.allow()

    last_message = messages[-1]
    if getattr(last_message, "type", None) != "ai":
        return ToolPolicyDecision.allow()

    tool_calls = getattr(last_message, "tool_calls", []) or []
    if len(tool_calls) != 1:
        return ToolPolicyDecision.allow()

    tool_call = tool_calls[0]
    signature = _tool_call_signature(tool_call)
    repeat_count = _count_trailing_identical_tool_calls(messages, signature)

    if repeat_count <= max_identical_repeats:
        return ToolPolicyDecision.allow()

    dialog_state = state.get("dialog_state", [])
    current_step = dialog_state[-1] if dialog_state else "current"
    tool_name = tool_call.get("name", "tool")
    content = (
        f"Blocked repeated identical tool call to '{tool_name}' in step '{current_step}'. "
        f"The same request has already been made {repeat_count - 1} times in a row and its prior result is already in context. "
        "Do not call the same tool again with the same arguments in this step. "
        "Use the existing tool result to continue the task, produce your structured output, or call CompleteOrEscalate if you truly cannot proceed."
    )
    return _blocked_decision(
        tool_call=tool_call,
        reason="repeated_tool_call",
        observed_calls=repeat_count,
        limit=max_identical_repeats,
        content=content,
        error_code="repeated_tool_call_blocked",
    )


def evaluate_parser_tool_budget(
    state: State,
    *,
    max_total_calls: int,
) -> ToolPolicyDecision:
    messages = state.get("messages", [])
    if not messages:
        return ToolPolicyDecision.allow()

    dialog_state = state.get("dialog_state", [])
    current_step = dialog_state[-1] if dialog_state else ""
    if current_step != "parser":
        return ToolPolicyDecision.allow()

    last_message = messages[-1]
    if getattr(last_message, "type", None) != "ai":
        return ToolPolicyDecision.allow()

    tool_calls = getattr(last_message, "tool_calls", []) or []
    if len(tool_calls) != 1:
        return ToolPolicyDecision.allow()

    tool_call = tool_calls[0]
    tool_name = tool_call.get("name", "")
    guarded_tools = {"read_docs", "web_search"}
    if tool_name not in guarded_tools:
        return ToolPolicyDecision.allow()

    total_calls = _count_step_tool_calls(messages, "parser", guarded_tools)
    if total_calls <= max_total_calls:
        return ToolPolicyDecision.allow()

    content = (
        "Blocked parser retrieval budget overflow. "
        f"In the current parser step, read_docs and web_search have already been called {total_calls - 1} times. "
        f"The total budget for these retrieval tools is {max_total_calls}. "
        "Do not continue searching. Use the existing retrieved material to finish the structured parsing result, "
        "or call CompleteOrEscalate if the remaining uncertainty is too high."
    )
    return _blocked_decision(
        tool_call=tool_call,
        reason="parser_tool_budget",
        observed_calls=total_calls,
        limit=max_total_calls,
        content=content,
        error_code="tool_budget_exceeded",
    )


def evaluate_tool_policy(state: State, policy: ToolExecutionPolicy) -> ToolPolicyDecision:
    parser_budget_decision = evaluate_parser_tool_budget(
        state,
        max_total_calls=policy.parser_max_retrieval_calls,
    )
    if parser_budget_decision.is_blocked:
        return parser_budget_decision

    return evaluate_repeated_tool_calls(
        state,
        max_identical_repeats=policy.max_identical_repeats,
    )
