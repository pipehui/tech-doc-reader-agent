from __future__ import annotations

from collections.abc import Hashable

from langgraph.graph import END, START, StateGraph

from .nodes import (
    assistant_node,
    create_entry_node,
    create_exit_node,
    create_finish_node,
    create_primary_tool_failure_node,
    store_plan,
)
from .budgeting import (
    WorkflowBudgetTracker,
    budgeted_request_start_node,
)
from .budget_termination import create_budget_termination_node
from .context_metrics import ContextMetricsTracker, context_metrics_request_start_node
from .reflection import route_after_tool_result
from .routing import (
    NEXT_STEP_ROUTE_MAP,
    make_primary_router,
    make_subagent_router,
    route_after_user_info,
    route_next_step,
)
from .specs import AgentSpec, ExecutionPolicy, GraphSpec
from .state import State
from .tool_nodes import create_tool_node_with_fallback


def _register_tool_node(
    builder: StateGraph,
    *,
    node_name: str,
    tools: tuple,
    execution_policy: ExecutionPolicy,
    budget_tracker: WorkflowBudgetTracker,
    continue_node: str,
    terminate_node: str,
) -> None:
    builder.add_node(
        node_name,
        create_tool_node_with_fallback(
            list(tools),
            execution_policy.tools,
            execution_policy.reflection,
            budget_tracker,
        ),
    )
    builder.add_conditional_edges(
        node_name,
        route_after_tool_result,
        {
            "continue": continue_node,
            "terminate": terminate_node,
            "budget_terminate": "budget_terminated",
        },
    )


def register_subagent(
    builder: StateGraph,
    spec: AgentSpec,
    execution_policy: ExecutionPolicy,
    budget_tracker: WorkflowBudgetTracker,
    context_tracker: ContextMetricsTracker,
) -> None:
    builder.add_node(spec.entry_node, create_entry_node(spec.display_name, spec.key))
    builder.add_node(
        spec.key,
        assistant_node(
            spec.assistant,
            scoped_messages=spec.scoped_messages,
            budget_tracker=budget_tracker,
            context_tracker=context_tracker,
        ),
    )
    builder.add_edge(spec.entry_node, spec.key)

    if spec.tools.safe:
        _register_tool_node(
            builder,
            node_name=spec.safe_tool_node,
            tools=spec.tools.safe,
            execution_policy=execution_policy,
            budget_tracker=budget_tracker,
            continue_node=spec.key,
            terminate_node=spec.leave_node,
        )

    if spec.tools.sensitive:
        _register_tool_node(
            builder,
            node_name=spec.sensitive_tool_node,
            tools=spec.tools.sensitive,
            execution_policy=execution_policy,
            budget_tracker=budget_tracker,
            continue_node=spec.key,
            terminate_node=spec.leave_node,
        )

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
        "budget_terminated": "budget_terminated",
    }
    if spec.tools.safe:
        route_targets[spec.safe_tool_node] = spec.safe_tool_node
    if spec.tools.sensitive:
        route_targets[spec.sensitive_tool_node] = spec.sensitive_tool_node
    builder.add_conditional_edges(spec.key, make_subagent_router(spec), route_targets)


def create_graph_builder(spec: GraphSpec) -> StateGraph:
    builder = StateGraph(State)
    builder.add_node(
        "budget_terminated",
        create_budget_termination_node(spec.budget_tracker),
    )
    builder.add_edge("budget_terminated", END)
    builder.add_node(
        "fetch_user_info",
        budgeted_request_start_node(
            context_metrics_request_start_node(
                spec.user_info_node,
                spec.context_tracker,
            ),
            spec.budget_tracker,
        ),
    )
    builder.add_edge(START, "fetch_user_info")

    for subagent in spec.subagents:
        register_subagent(
            builder,
            subagent,
            spec.execution_policy,
            spec.budget_tracker,
            spec.context_tracker,
        )

    builder.add_node(
        "primary_assistant",
        assistant_node(
            spec.primary.assistant,
            budget_tracker=spec.budget_tracker,
            context_tracker=spec.context_tracker,
        ),
    )
    _register_tool_node(
        builder,
        node_name="primary_assistant_tools",
        tools=spec.primary.tools.safe,
        execution_policy=spec.execution_policy,
        budget_tracker=spec.budget_tracker,
        continue_node="primary_assistant",
        terminate_node="primary_tool_failure",
    )
    _register_tool_node(
        builder,
        node_name="primary_assistant_sensitive_tools",
        tools=spec.primary.tools.sensitive,
        execution_policy=spec.execution_policy,
        budget_tracker=spec.budget_tracker,
        continue_node="primary_assistant",
        terminate_node="primary_tool_failure",
    )
    builder.add_node("primary_tool_failure", create_primary_tool_failure_node())
    builder.add_edge("primary_tool_failure", END)
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
        "primary_tool_failure": "primary_tool_failure",
        "budget_terminated": "budget_terminated",
        END: END,
    }
    sensitive_tool_names = frozenset(tool.name for tool in spec.primary.tools.sensitive)
    builder.add_conditional_edges(
        "primary_assistant",
        make_primary_router(sensitive_tool_names),
        primary_routes,
    )
    builder.add_conditional_edges("store_plan", route_next_step, NEXT_STEP_ROUTE_MAP)
    return builder


def build_multi_agentic_graph(checkpointer, spec: GraphSpec):
    return create_graph_builder(spec).compile(
        checkpointer=checkpointer,
        interrupt_before=list(spec.interrupt_nodes),
    )
