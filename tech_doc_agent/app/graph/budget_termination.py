from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol

from langchain_core.messages import AIMessage, ToolCall, ToolMessage

from tech_doc_agent.app.core.errors import Conflict
from tech_doc_agent.app.core.execution_budget import BudgetDecision

from .state import State


class BudgetEventSink(Protocol):
    @property
    def event_logger(self) -> Callable[..., None]: ...


def create_budget_termination_node(
    event_sink: BudgetEventSink,
) -> Callable[[State], dict[str, Any]]:
    def terminate(state: State) -> dict[str, Any]:
        decision = BudgetDecision.from_state(state.get("budget_termination"))
        dialog_state = state.get("dialog_state", [])
        agent = dialog_state[-1] if dialog_state else "primary"
        event_sink.event_logger(
            "budget.terminated",
            agent=agent,
            scope=decision.scope,
            dimension=decision.dimension,
            phase=decision.phase,
            operation=decision.operation,
            reason=decision.reason,
            observed=decision.observed,
            limit=decision.limit,
        )
        update: dict[str, Any] = {
            "messages": [
                AIMessage(
                    content=_termination_message(decision),
                    name=agent,
                )
            ],
            "workflow_plan": [],
            "plan_index": 0,
            "budget_status": "terminated",
            "budget_termination": decision.to_state(),
            "budget_usage": state.get("budget_usage", {}),
            "budget_usage_delta": {},
            "reflection_status": "idle",
            "reflection_tool": "",
            "reflection_error_code": "",
            "reflection_terminal_reason": "",
        }
        if dialog_state:
            update["dialog_state"] = "pop"
        return update

    return terminate


def mark_budget_terminating(
    update: dict[str, Any],
    decision: BudgetDecision,
) -> dict[str, Any]:
    return {
        **update,
        "budget_status": "terminating",
        "budget_termination": decision.to_state(),
    }


def budget_closed_tool_messages(
    tool_calls: Iterable[ToolCall],
    decision: BudgetDecision,
) -> list[ToolMessage]:
    results = []
    for tool_call in tool_calls:
        tool_name = tool_call.get("name") or "tool"
        error = Conflict(
            "The tool was not executed because the execution budget stopped the workflow.",
            code=decision.error_code,
            dependency="execution_budget",
            tool=tool_name,
            cause_type="ExecutionBudgetPolicy",
        )
        results.append(
            ToolMessage(
                tool_call_id=tool_call["id"],
                name=tool_name,
                status="error",
                content=error.to_json(),
                artifact={
                    "error": error.to_payload(),
                    "budget_termination": decision.to_state(),
                },
            )
        )
    return results


def last_ai_tool_calls(messages: Iterable[Any]) -> list[ToolCall]:
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage):
            return list(message.tool_calls or [])
    return []


def update_messages(update: dict[str, Any]) -> list[Any]:
    messages = update.get("messages")
    if messages is None:
        return []
    if isinstance(messages, (list, tuple)):
        return list(messages)
    return [messages]


def _termination_message(decision: BudgetDecision) -> str:
    if decision.reason == "usage_unreported":
        return (
            "本次工作流已安全停止：模型未上报继续执行所需的用量信息，"
            f"因此无法确认下一次调用不会超过 {decision.dimension} 上限。"
            "已完成的步骤和中间结果仍保留在当前会话中，你可以调整预算配置后继续。"
        )
    if decision.scope == "request":
        detail = (
            f"本次请求耗时约 {decision.observed} 秒，已达到 {decision.limit} 秒的请求上限"
        )
    else:
        detail = (
            f"工作流的 {decision.dimension} 已达到预算边界"
            f"（观测值 {decision.observed}，上限 {decision.limit}）"
        )
    return (
        f"本次工作流已在完成当前原子步骤后安全停止：{detail}。"
        "已完成的步骤和中间结果仍保留在当前会话中；如需继续，请调整预算后发起新请求。"
    )


__all__ = [
    "budget_closed_tool_messages",
    "create_budget_termination_node",
    "last_ai_tool_calls",
    "mark_budget_terminating",
    "update_messages",
]
