from collections import defaultdict

from langgraph.checkpoint.memory import MemorySaver

from tech_doc_agent.app.graph import build_multi_agentic_graph


NEXT_STEP_TARGETS = {
    "enter_parser",
    "enter_relation",
    "enter_explanation",
    "enter_examination",
    "enter_summary",
    "__end__",
}

SUBAGENT_ROUTE_TARGETS = {
    "parser": {
        "parser_assistant_safe_tools",
        "parser_assistant_sensitive_tools",
        "leave_parser",
        "finish_parser",
    },
    "relation": {
        "relation_assistant_safe_tools",
        "leave_relation",
        "finish_relation",
    },
    "explanation": {
        "explanation_assistant_safe_tools",
        "leave_explanation",
        "finish_explanation",
    },
    "examination": {
        "examination_assistant_safe_tools",
        "examination_assistant_sensitive_tools",
        "leave_examination",
        "finish_examination",
    },
    "summary": {
        "summary_assistant_safe_tools",
        "summary_assistant_sensitive_tools",
        "leave_summary",
        "finish_summary",
    },
}

EXPECTED_INTERRUPTS = {
    "parser_assistant_sensitive_tools",
    "examination_assistant_sensitive_tools",
    "summary_assistant_sensitive_tools",
    "primary_assistant_sensitive_tools",
}


def test_compiled_graph_preserves_subagent_topology_and_interrupts():
    graph = build_multi_agentic_graph(MemorySaver())
    graph_view = graph.get_graph()
    outgoing: dict[str, set[str]] = defaultdict(set)
    conditional_sources: set[str] = set()

    for edge in graph_view.edges:
        outgoing[edge.source].add(edge.target)
        if edge.conditional:
            conditional_sources.add(edge.source)

    assert outgoing["__start__"] == {"fetch_user_info"}
    assert outgoing["fetch_user_info"] == {"primary_assistant", "enter_examination"}
    assert outgoing["primary_assistant"] == {
        "store_plan",
        "primary_assistant_tools",
        "primary_assistant_sensitive_tools",
        *NEXT_STEP_TARGETS,
    }
    assert outgoing["store_plan"] == NEXT_STEP_TARGETS
    assert outgoing["primary_assistant_tools"] == {"primary_assistant"}
    assert outgoing["primary_assistant_sensitive_tools"] == {"primary_assistant"}

    for agent, route_targets in SUBAGENT_ROUTE_TARGETS.items():
        assert outgoing[f"enter_{agent}"] == {agent}
        assert outgoing[agent] == route_targets
        assert outgoing[f"leave_{agent}"] == {"primary_assistant"}
        assert outgoing[f"finish_{agent}"] == NEXT_STEP_TARGETS

        safe_node = f"{agent}_assistant_safe_tools"
        assert outgoing[safe_node] == {agent}

        sensitive_node = f"{agent}_assistant_sensitive_tools"
        if sensitive_node in route_targets:
            assert outgoing[sensitive_node] == {agent}
        else:
            assert sensitive_node not in graph_view.nodes

    assert conditional_sources == {
        "fetch_user_info",
        "primary_assistant",
        "store_plan",
        *(f"finish_{agent}" for agent in SUBAGENT_ROUTE_TARGETS),
        *SUBAGENT_ROUTE_TARGETS,
    }
    assert set(graph.interrupt_before_nodes) == EXPECTED_INTERRUPTS
    assert graph.interrupt_after_nodes == []
