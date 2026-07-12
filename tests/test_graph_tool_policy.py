from langchain_core.messages import AIMessage, ToolMessage

from tech_doc_agent.app.graph.tool_policy import (
    maybe_block_parser_tool_budget,
    maybe_block_repeated_tool_calls,
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

    blocked = maybe_block_repeated_tool_calls(state)

    assert blocked is not None
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

    assert maybe_block_repeated_tool_calls(state) is None


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

    blocked = maybe_block_parser_tool_budget(state, max_total_calls=2)

    assert blocked is not None
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

    assert maybe_block_parser_tool_budget(state, max_total_calls=0) is None
