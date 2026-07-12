from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any, Literal, cast

from langgraph.graph import END
from langgraph.prebuilt import tools_condition

from tech_doc_agent.app.graph.commands import (
    CompleteOrEscalate,
    PlanWorkflow,
    ToDocParserAssistant,
    ToExaminationAssistant,
    ToExplanationAssistant,
    ToRelationAssistant,
    ToSummaryAssistant,
)
from tech_doc_agent.app.services.message_scope import should_route_to_examination

from .specs import AgentSpec
from .state import State


STEP_ENTRY_TARGETS = {
    "parser": "enter_parser",
    "relation": "enter_relation",
    "explanation": "enter_explanation",
    "examination": "enter_examination",
    "summary": "enter_summary",
}

NextStepTarget = Literal[
    "enter_parser",
    "enter_relation",
    "enter_explanation",
    "enter_examination",
    "enter_summary",
    "__end__",
]

PrimaryRouteTarget = Literal[
    "store_plan",
    "primary_assistant_tools",
    "primary_assistant_sensitive_tools",
    "primary_tool_failure",
    "enter_parser",
    "enter_explanation",
    "enter_relation",
    "enter_examination",
    "enter_summary",
    "budget_terminated",
    "__end__",
]

NEXT_STEP_ROUTE_MAP: dict[Hashable, str] = {
    **{target: target for target in STEP_ENTRY_TARGETS.values()},
    END: END,
}


def route_next_step(state: State) -> NextStepTarget:
    plan = state.get("workflow_plan", [])
    index = state.get("plan_index", 0)

    if index >= len(plan):
        return cast(NextStepTarget, END)

    return cast(NextStepTarget, STEP_ENTRY_TARGETS.get(plan[index], END))


def route_after_user_info(state: State) -> Literal[
    "enter_examination",
    "primary_assistant",
]:
    if should_route_to_examination(state):
        return "enter_examination"
    return "primary_assistant"


def make_subagent_router(spec: AgentSpec) -> Callable[[State], str]:
    safe_tool_names = frozenset(tool.name for tool in spec.tools.safe)

    def route_subagent(state: State) -> str:
        if state.get("budget_status") == "terminating":
            return "budget_terminated"
        route = tools_condition(cast(dict[str, Any], state))
        if route == END:
            return spec.finish_node

        tool_calls = list(getattr(state["messages"][-1], "tool_calls", []) or [])
        did_cancel = any(tool_call["name"] == CompleteOrEscalate.__name__ for tool_call in tool_calls)
        if did_cancel:
            return spec.leave_node

        if state.get("reflection_status") == "finalizing":
            return spec.leave_node

        if spec.tools.safe and all(tool_call["name"] in safe_tool_names for tool_call in tool_calls):
            return spec.safe_tool_node

        if spec.tools.sensitive:
            return spec.sensitive_tool_node

        if spec.tools.safe:
            return spec.safe_tool_node

        raise RuntimeError(f"Agent '{spec.key}' requested a tool outside its declared policy.")

    route_subagent.__name__ = f"route_{spec.key}"
    return route_subagent


def make_primary_router(sensitive_tool_names: frozenset[str]) -> Callable[[State], PrimaryRouteTarget]:
    def route_primary_assistant(state: State) -> PrimaryRouteTarget:
        if state.get("budget_status") == "terminating":
            return "budget_terminated"
        route = tools_condition(cast(dict[str, Any], state))
        if route == END:
            return cast(PrimaryRouteTarget, END)

        tool_calls = list(getattr(state["messages"][-1], "tool_calls", []) or [])
        if not tool_calls:
            return cast(PrimaryRouteTarget, END)

        if state.get("reflection_status") == "finalizing":
            return "primary_tool_failure"

        tool_name = tool_calls[0]["name"]
        if tool_name == PlanWorkflow.__name__:
            return "store_plan"
        if tool_name == ToDocParserAssistant.__name__:
            return "enter_parser"
        if tool_name == ToExplanationAssistant.__name__:
            return "enter_explanation"
        if tool_name == ToRelationAssistant.__name__:
            return "enter_relation"
        if tool_name == ToExaminationAssistant.__name__:
            return "enter_examination"
        if tool_name == ToSummaryAssistant.__name__:
            return "enter_summary"
        if tool_name in sensitive_tool_names:
            return "primary_assistant_sensitive_tools"
        return "primary_assistant_tools"

    return route_primary_assistant


route_primary_assistant = make_primary_router(
    frozenset({"upsert_learning_history", "update_user_profile"})
)
