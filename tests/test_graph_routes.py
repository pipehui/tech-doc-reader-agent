import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END

from tech_doc_agent.app.graph import (
    route_examination,
    route_explanation,
    route_next_step,
    route_parser,
    route_relation,
    route_summary,
)


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
    ("route", "finish_target"),
    [
        (route_parser, "finish_parser"),
        (route_relation, "finish_relation"),
        (route_explanation, "finish_explanation"),
        (route_examination, "finish_examination"),
        (route_summary, "finish_summary"),
    ],
)
def test_subagent_route_finishes_when_assistant_returns_content(route, finish_target):
    state = {"messages": [AIMessage(content="done")]}

    assert route(state) == finish_target


@pytest.mark.parametrize(
    ("route", "leave_target"),
    [
        (route_parser, "leave_parser"),
        (route_relation, "leave_relation"),
        (route_explanation, "leave_explanation"),
        (route_examination, "leave_examination"),
        (route_summary, "leave_summary"),
    ],
)
def test_subagent_route_leaves_on_complete_or_escalate(route, leave_target):
    assert route(_state_with_tool_call("CompleteOrEscalate")) == leave_target


@pytest.mark.parametrize(
    ("route", "safe_tool", "safe_target"),
    [
        (route_parser, "read_docs", "parser_assistant_safe_tools"),
        (route_relation, "read_all_learning_history", "relation_assistant_safe_tools"),
        (route_explanation, "read_docs", "explanation_assistant_safe_tools"),
        (route_examination, "read_docs", "examination_assistant_safe_tools"),
        (route_summary, "read_learning_history", "summary_assistant_safe_tools"),
    ],
)
def test_subagent_route_sends_safe_tools_to_safe_node(route, safe_tool, safe_target):
    assert route(_state_with_tool_call(safe_tool)) == safe_target


@pytest.mark.parametrize(
    ("route", "sensitive_tool", "sensitive_target"),
    [
        (route_parser, "save_docs", "parser_assistant_sensitive_tools"),
        (route_examination, "upsert_learning_history", "examination_assistant_sensitive_tools"),
        (route_summary, "upsert_learning_state", "summary_assistant_sensitive_tools"),
    ],
)
def test_subagent_route_sends_sensitive_tools_to_sensitive_node(route, sensitive_tool, sensitive_target):
    assert route(_state_with_tool_call(sensitive_tool)) == sensitive_target


@pytest.mark.parametrize(
    ("route", "safe_target"),
    [
        (route_relation, "relation_assistant_safe_tools"),
        (route_explanation, "explanation_assistant_safe_tools"),
    ],
)
def test_read_only_subagent_keeps_unknown_tool_calls_on_existing_safe_fallback(route, safe_target):
    assert route(_state_with_tool_call("unexpected_tool")) == safe_target


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
