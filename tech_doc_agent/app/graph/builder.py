from __future__ import annotations

from collections.abc import Hashable

from langgraph.graph import END, START, StateGraph

from tech_doc_agent.app.services.assistants.examination_assistant import (
    examination_assistant,
    examination_assistant_safe_tools,
    examination_assistant_sensitive_tools,
)
from tech_doc_agent.app.services.assistants.explanation_assistant import (
    explanation_assistant,
    explanation_assistant_safe_tools,
)
from tech_doc_agent.app.services.assistants.parser_assistant import (
    parser_assistant,
    parser_assistant_safe_tools,
    parser_assistant_sensitive_tools,
)
from tech_doc_agent.app.services.assistants.primary_assistant import (
    primary_assistant,
    primary_assistant_sensitive_tools,
    primary_assistant_tools,
)
from tech_doc_agent.app.services.assistants.relation_assistant import (
    relation_assistant,
    relation_assistant_safe_tools,
)
from tech_doc_agent.app.services.assistants.summary_assistant import (
    summary_assistant,
    summary_assistant_safe_tools,
    summary_assistant_sensitive_tools,
)
from tech_doc_agent.app.services.utils import (
    create_entry_node,
    create_exit_node,
    create_finish_node,
    create_tool_node_with_fallback,
    store_plan,
)

from .nodes import assistant_node, user_info
from .routing import (
    NEXT_STEP_ROUTE_MAP,
    make_subagent_router,
    route_after_user_info,
    route_next_step,
    route_primary_assistant,
)
from .specs import AgentSpec, CompletionPolicy, ToolPolicy
from .state import State


SUBAGENT_SPECS = (
    AgentSpec(
        key="parser",
        display_name="Parser Assistant",
        assistant=parser_assistant,
        tools=ToolPolicy(
            safe=tuple(parser_assistant_safe_tools),
            sensitive=tuple(parser_assistant_sensitive_tools),
        ),
        completion=CompletionPolicy(result_key="parser_result", structured_kind="parser"),
    ),
    AgentSpec(
        key="explanation",
        display_name="Explanation Assitant",
        assistant=explanation_assistant,
        tools=ToolPolicy(safe=tuple(explanation_assistant_safe_tools)),
    ),
    AgentSpec(
        key="relation",
        display_name="Relation Assitant",
        assistant=relation_assistant,
        tools=ToolPolicy(safe=tuple(relation_assistant_safe_tools)),
        completion=CompletionPolicy(result_key="relation_result", structured_kind="relation"),
    ),
    AgentSpec(
        key="examination",
        display_name="Examination Assitant",
        assistant=examination_assistant,
        tools=ToolPolicy(
            safe=tuple(examination_assistant_safe_tools),
            sensitive=tuple(examination_assistant_sensitive_tools),
        ),
        completion=CompletionPolicy(result_key="examination_context"),
    ),
    AgentSpec(
        key="summary",
        display_name="Summary Assitant",
        assistant=summary_assistant,
        tools=ToolPolicy(
            safe=tuple(summary_assistant_safe_tools),
            sensitive=tuple(summary_assistant_sensitive_tools),
        ),
        scoped_messages=False,
    ),
)

SUBAGENT_ROUTES = {spec.key: make_subagent_router(spec) for spec in SUBAGENT_SPECS}
route_parser = SUBAGENT_ROUTES["parser"]
route_relation = SUBAGENT_ROUTES["relation"]
route_explanation = SUBAGENT_ROUTES["explanation"]
route_examination = SUBAGENT_ROUTES["examination"]
route_summary = SUBAGENT_ROUTES["summary"]

interrupt_nodes = [
    *(spec.sensitive_tool_node for spec in SUBAGENT_SPECS if spec.tools.sensitive),
    "primary_assistant_sensitive_tools",
]


def register_subagent(builder: StateGraph, spec: AgentSpec) -> None:
    builder.add_node(spec.entry_node, create_entry_node(spec.display_name, spec.key))
    builder.add_node(spec.key, assistant_node(spec.assistant, scoped_messages=spec.scoped_messages))
    builder.add_edge(spec.entry_node, spec.key)

    if spec.tools.safe:
        builder.add_node(spec.safe_tool_node, create_tool_node_with_fallback(list(spec.tools.safe)))
        builder.add_edge(spec.safe_tool_node, spec.key)

    if spec.tools.sensitive:
        builder.add_node(spec.sensitive_tool_node, create_tool_node_with_fallback(list(spec.tools.sensitive)))
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
    builder.add_conditional_edges(spec.key, SUBAGENT_ROUTES[spec.key], route_targets)


def create_graph_builder() -> StateGraph:
    builder = StateGraph(State)
    builder.add_node("fetch_user_info", user_info)
    builder.add_edge(START, "fetch_user_info")

    for spec in SUBAGENT_SPECS:
        register_subagent(builder, spec)

    builder.add_node("primary_assistant", assistant_node(primary_assistant))
    builder.add_node("primary_assistant_tools", create_tool_node_with_fallback(primary_assistant_tools))
    builder.add_node(
        "primary_assistant_sensitive_tools",
        create_tool_node_with_fallback(primary_assistant_sensitive_tools),
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
    builder.add_conditional_edges(
        "primary_assistant",
        route_primary_assistant,
        primary_routes,
    )
    builder.add_conditional_edges("store_plan", route_next_step, NEXT_STEP_ROUTE_MAP)
    builder.add_edge("primary_assistant_tools", "primary_assistant")
    builder.add_edge("primary_assistant_sensitive_tools", "primary_assistant")
    return builder


def build_multi_agentic_graph(checkpointer):
    return create_graph_builder().compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_nodes,
    )
