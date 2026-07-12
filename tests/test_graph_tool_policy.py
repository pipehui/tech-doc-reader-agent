from langchain_core.messages import AIMessage, ToolMessage
import pytest

from tech_doc_agent.app.graph.specs import ReflectionPolicy, ToolExecutionPolicy
from tech_doc_agent.app.graph.tool_policy import (
    evaluate_parser_tool_budget,
    evaluate_repeated_tool_calls,
    evaluate_tool_policy,
)


def _ai_tool_call(tool_name: str, call_id: str, args: dict | None = None, *, name: str = "parser"):
    return AIMessage(
        content="",
        name=name,
        tool_calls=[{"name": tool_name, "args": args or {}, "id": call_id}],
    )


def _tool_result(call_id: str):
    return ToolMessage(content="result", tool_call_id=call_id)


def test_repeated_tool_policy_blocks_third_identical_call():
    state = {
        "messages": [
            _ai_tool_call("read_docs", "call-1", {"query": "StateGraph"}),
            _tool_result("call-1"),
            _ai_tool_call("read_docs", "call-2", {"query": "StateGraph"}),
            _tool_result("call-2"),
            _ai_tool_call("read_docs", "call-3", {"query": "StateGraph"}),
        ],
        "dialog_state": ["parser"],
    }

    decision = evaluate_repeated_tool_calls(state, max_identical_repeats=2)
    blocked = decision.to_graph_update()

    assert decision.action == "block"
    assert decision.reason == "repeated_tool_call"
    assert decision.observed_calls == 3
    assert decision.limit == 2
    assert blocked["messages"][0].tool_call_id == "call-3"
    assert blocked["messages"][0].status == "error"
    assert "Blocked repeated identical tool call" in blocked["messages"][0].content
    assert blocked["messages"][0].artifact["error"] == {
        "status": "error",
        "code": "repeated_tool_call_blocked",
        "retryable": False,
        "safe_message": blocked["messages"][0].content,
        "dependency": None,
        "tool": "read_docs",
        "cause_type": "ToolPolicy",
    }


def test_repeated_tool_policy_allows_changed_arguments():
    state = {
        "messages": [
            _ai_tool_call("read_docs", "call-1", {"query": "StateGraph"}),
            _tool_result("call-1"),
            _ai_tool_call("read_docs", "call-2", {"query": "Checkpoint"}),
        ],
        "dialog_state": ["parser"],
    }

    decision = evaluate_repeated_tool_calls(state, max_identical_repeats=2)

    assert decision.action == "allow"
    assert decision.is_blocked is False
    with pytest.raises(ValueError, match="Only block decisions"):
        decision.to_graph_update()


def test_parser_budget_blocks_call_after_configured_total():
    state = {
        "messages": [
            _ai_tool_call("read_docs", "call-1"),
            _tool_result("call-1"),
            _ai_tool_call("web_search", "call-2"),
            _tool_result("call-2"),
            _ai_tool_call("read_docs", "call-3"),
        ],
        "dialog_state": ["parser"],
    }

    decision = evaluate_parser_tool_budget(state, max_total_calls=2)
    blocked = decision.to_graph_update()

    assert decision.action == "block"
    assert decision.reason == "parser_tool_budget"
    assert decision.observed_calls == 3
    assert decision.limit == 2
    assert blocked["messages"][0].tool_call_id == "call-3"
    assert blocked["messages"][0].status == "error"
    assert "parser retrieval budget overflow" in blocked["messages"][0].content
    assert blocked["messages"][0].artifact["error"]["code"] == "tool_budget_exceeded"
    assert blocked["messages"][0].artifact["error"]["retryable"] is False


def test_parser_budget_does_not_apply_outside_parser_step():
    state = {
        "messages": [_ai_tool_call("read_docs", "call-1", name="explanation")],
        "dialog_state": ["explanation"],
    }

    decision = evaluate_parser_tool_budget(state, max_total_calls=0)

    assert decision.action == "allow"


def test_combined_policy_applies_parser_budget_before_repeat_limit():
    state = {
        "messages": [_ai_tool_call("read_docs", "call-1")],
        "dialog_state": ["parser"],
    }

    decision = evaluate_tool_policy(
        state,
        ToolExecutionPolicy(
            max_identical_repeats=0,
            parser_max_retrieval_calls=0,
        ),
    )

    assert decision.action == "block"
    assert decision.reason == "parser_tool_budget"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_identical_repeats", -1),
        ("parser_max_retrieval_calls", -1),
    ],
)
def test_tool_execution_policy_rejects_negative_limits(field, value):
    values = {
        "max_identical_repeats": 2,
        "parser_max_retrieval_calls": 6,
    }
    values[field] = value

    with pytest.raises(ValueError, match="must be non-negative"):
        ToolExecutionPolicy(**values)


def test_reflection_policy_rejects_invalid_rounds_and_error_codes():
    with pytest.raises(ValueError, match="max_rounds"):
        ReflectionPolicy(max_rounds=-1)

    with pytest.raises(ValueError, match="repairable_error_codes"):
        ReflectionPolicy(repairable_error_codes=frozenset())

    with pytest.raises(ValueError, match="repairable_error_codes"):
        ReflectionPolicy(repairable_error_codes=frozenset({""}))

    with pytest.raises(ValueError, match="repairable_error_codes"):
        ReflectionPolicy(repairable_error_codes=frozenset({" validation_error "}))
