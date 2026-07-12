from __future__ import annotations

from collections.abc import Hashable

from langgraph.graph import END, START, StateGraph

from .nodes import assistant_node, create_entry_node, create_exit_node, create_finish_node, store_plan
from .routing import (
    NEXT_STEP_ROUTE_MAP,
    make_primary_router,
    make_subagent_router,
    route_after_user_info,
    route_next_step,
)
from .specs import AgentSpec, GraphSpec, ToolExecutionPolicy
from .state import State
from .tool_nodes import create_tool_node_with_fallback


def register_subagent(
    builder: StateGraph,
    spec: AgentSpec,
    tool_execution_policy: ToolExecutionPolicy,
) -> None:
    builder.add_node(spec.entry_node, create_entry_node(spec.display_name, spec.key))
    builder.add_node(spec.key, assistant_node(spec.assistant, scoped_messages=spec.scoped_messages))
    builder.add_edge(spec.entry_node, spec.key)

    if spec.tools.safe:
        builder.add_node(
            spec.safe_tool_node,
            create_tool_node_with_fallback(list(spec.tools.safe), tool_execution_policy),
        )
        builder.add_edge(spec.safe_tool_node, spec.key)

    if spec.tools.sensitive:
        builder.add_node(
            spec.sensitive_tool_node,
            create_tool_node_with_fallback(list(spec.tools.sensitive), tool_execution_policy),
        )
        builder.add_edge(spec.sensitive_tool_node, spec.key)

    builder.add_node(spec.leave_node, create_exit_node())
    builder.add_edge(spec.leave_node, "primary_assistant")
    builder.add_node(
        spec.finish_node,
        create_finish_node(
            spec.completion.result_key,
            structured_kind=spec.completion.structured_kind,
        ),
    )
    builder.add_conditional_edges(spec.finish_node, route_next_step, NEXT_STEP_ROUTE_MAP)

    route_targets: dict[Hashable, str] = {
        spec.leave_node: spec.leave_node,
        spec.finish_node: spec.finish_node,
    }
    if spec.tools.safe:
        route_targets[spec.safe_tool_node] = spec.safe_tool_node
    if spec.tools.sensitive:
        route_targets[spec.sensitive_tool_node] = spec.sensitive_tool_node
    builder.add_conditional_edges(spec.key, make_subagent_router(spec), route_targets)


def create_graph_builder(spec: GraphSpec) -> StateGraph:
    builder = StateGraph(State)
    builder.add_node("fetch_user_info", spec.user_info_node)
    builder.add_edge(START, "fetch_user_info")

    for subagent in spec.subagents:
        register_subagent(builder, subagent, spec.tool_execution_policy)

    builder.add_node("primary_assistant", assistant_node(spec.primary.assistant))
    builder.add_node(
        "primary_assistant_tools",
        create_tool_node_with_fallback(list(spec.primary.tools.safe), spec.tool_execution_policy),
    )
    builder.add_node(
        "primary_assistant_sensitive_tools",
        create_tool_node_with_fallback(list(spec.primary.tools.sensitive), spec.tool_execution_policy),
    )
    builder.add_node("store_plan", store_plan)
    fetch_user_info_routes: dict[Hashable, str] = {
        "enter_examination": "enter_examination",
        "primary_assistant": "primary_assistant",
    }
    builder.add_conditional_edges(
        "fetch_user_info",
        route_after_user_info,
        fetch_user_info_routes,
    )
    primary_routes: dict[Hashable, str] = {
        "store_plan": "store_plan",
        "enter_parser": "enter_parser",
        "enter_explanation": "enter_explanation",
        "enter_relation": "enter_relation",
        "enter_examination": "enter_examination",
        "enter_summary": "enter_summary",
        "primary_assistant_tools": "primary_assistant_tools",
        "primary_assistant_sensitive_tools": "primary_assistant_sensitive_tools",
        END: END,
    }
    sensitive_tool_names = frozenset(tool.name for tool in spec.primary.tools.sensitive)
    builder.add_conditional_edges(
        "primary_assistant",
        make_primary_router(sensitive_tool_names),
        primary_routes,
    )
    builder.add_conditional_edges("store_plan", route_next_step, NEXT_STEP_ROUTE_MAP)
    builder.add_edge("primary_assistant_tools", "primary_assistant")
    builder.add_edge("primary_assistant_sensitive_tools", "primary_assistant")
    return builder


def build_multi_agentic_graph(checkpointer, spec: GraphSpec):
    return create_graph_builder(spec).compile(
        checkpointer=checkpointer,
        interrupt_before=list(spec.interrupt_nodes),
    )
