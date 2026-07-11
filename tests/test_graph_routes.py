import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END

from tech_doc_agent.app.graph import make_primary_router, make_subagent_router, route_next_step


def _state_with_tool_call(tool_name: str) -> dict:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": tool_name, "args": {}, "id": "call-1"}],
            )
        ]
    }


@pytest.mark.parametrize(
    ("agent", "finish_target"),
    [
        ("parser", "finish_parser"),
        ("relation", "finish_relation"),
        ("explanation", "finish_explanation"),
        ("examination", "finish_examination"),
        ("summary", "finish_summary"),
    ],
)
def test_subagent_route_finishes_when_assistant_returns_content(graph_spec, agent, finish_target):
    state = {"messages": [AIMessage(content="done")]}

    assert _route(graph_spec, agent)(state) == finish_target


@pytest.mark.parametrize(
    ("agent", "leave_target"),
    [
        ("parser", "leave_parser"),
        ("relation", "leave_relation"),
        ("explanation", "leave_explanation"),
        ("examination", "leave_examination"),
        ("summary", "leave_summary"),
    ],
)
def test_subagent_route_leaves_on_complete_or_escalate(graph_spec, agent, leave_target):
    assert _route(graph_spec, agent)(_state_with_tool_call("CompleteOrEscalate")) == leave_target


@pytest.mark.parametrize(
    ("agent", "safe_tool", "safe_target"),
    [
        ("parser", "read_docs", "parser_assistant_safe_tools"),
        ("relation", "read_all_learning_history", "relation_assistant_safe_tools"),
        ("explanation", "read_docs", "explanation_assistant_safe_tools"),
        ("examination", "read_docs", "examination_assistant_safe_tools"),
        ("summary", "read_learning_history", "summary_assistant_safe_tools"),
    ],
)
def test_subagent_route_sends_safe_tools_to_safe_node(graph_spec, agent, safe_tool, safe_target):
    assert _route(graph_spec, agent)(_state_with_tool_call(safe_tool)) == safe_target


@pytest.mark.parametrize(
    ("agent", "sensitive_tool", "sensitive_target"),
    [
        ("parser", "save_docs", "parser_assistant_sensitive_tools"),
        ("examination", "upsert_learning_history", "examination_assistant_sensitive_tools"),
        ("summary", "upsert_learning_state", "summary_assistant_sensitive_tools"),
    ],
)
def test_subagent_route_sends_sensitive_tools_to_sensitive_node(
    graph_spec,
    agent,
    sensitive_tool,
    sensitive_target,
):
    assert _route(graph_spec, agent)(_state_with_tool_call(sensitive_tool)) == sensitive_target


@pytest.mark.parametrize(
    ("agent", "safe_target"),
    [
        ("relation", "relation_assistant_safe_tools"),
        ("explanation", "explanation_assistant_safe_tools"),
    ],
)
def test_read_only_subagent_keeps_unknown_tool_calls_on_existing_safe_fallback(graph_spec, agent, safe_target):
    assert _route(graph_spec, agent)(_state_with_tool_call("unexpected_tool")) == safe_target


@pytest.mark.parametrize(
    ("step", "target"),
    [
        ("parser", "enter_parser"),
        ("relation", "enter_relation"),
        ("explanation", "enter_explanation"),
        ("examination", "enter_examination"),
        ("summary", "enter_summary"),
    ],
)
def test_route_next_step_maps_workflow_step_to_entry_node(step, target):
    assert route_next_step({"workflow_plan": [step], "plan_index": 0}) == target


def test_route_next_step_ends_for_completed_or_unknown_plan():
    assert route_next_step({"workflow_plan": ["parser"], "plan_index": 1}) == END
    assert route_next_step({"workflow_plan": ["unknown"], "plan_index": 0}) == END


def test_primary_router_uses_injected_sensitive_tool_names():
    route = make_primary_router(frozenset({"custom_sensitive_write"}))

    assert route(_state_with_tool_call("custom_sensitive_write")) == "primary_assistant_sensitive_tools"
    assert route(_state_with_tool_call("read_user_profile")) == "primary_assistant_tools"


def _route(graph_spec, agent: str):
    spec = next(spec for spec in graph_spec.subagents if spec.key == agent)
    return make_subagent_router(spec)
