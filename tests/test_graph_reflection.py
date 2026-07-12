import json

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ValidationError as PydanticValidationError

import tech_doc_agent.app.graph.reflection as reflection_module
from tech_doc_agent.app.graph.assistant_execution import assistant_node
from tech_doc_agent.app.graph.nodes import (
    create_exit_node,
    create_primary_tool_failure_node,
    create_user_info_node,
)
from tech_doc_agent.app.graph.reflection import (
    apply_reflection_policy,
    route_after_tool_result,
    safe_validation_repair_context,
)
from tech_doc_agent.app.graph.specs import ReflectionPolicy, ToolExecutionPolicy
from tech_doc_agent.app.graph.state import State
from tech_doc_agent.app.graph.tool_nodes import create_tool_node_with_fallback


TOOL_POLICY = ToolExecutionPolicy(
    max_identical_repeats=2,
    parser_max_retrieval_calls=6,
)


@tool
def needs_count(count: int) -> str:
    """Return a validated integer count."""
    return str(count)


@tool
def always_times_out(query: str) -> str:
    """Raise a deterministic transport-style timeout."""
    raise TimeoutError(f"private endpoint for {query}")


def _error_message(code: str, *, retryable: bool = False, content: str = "safe") -> ToolMessage:
    payload = {
        "status": "error",
        "code": code,
        "retryable": retryable,
        "safe_message": "The tool call failed safely.",
        "dependency": "test_provider",
        "tool": "needs_count",
        "cause_type": "InjectedFailure",
    }
    return ToolMessage(
        name="needs_count",
        content=content,
        tool_call_id="call-1",
        status="error",
        artifact={"error": payload},
    )


def test_validation_error_starts_one_explicit_argument_repair_round(monkeypatch):
    events = []
    monkeypatch.setattr(
        reflection_module,
        "log_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )
    state = {
        "messages": [],
        "dialog_state": ["parser"],
        "reflection_rounds_used": 0,
    }

    update = apply_reflection_policy(
        state,
        {"messages": [_error_message("validation_error")]},
        ReflectionPolicy(max_rounds=1),
    )

    assert update["reflection_status"] == "repairing"
    assert update["reflection_rounds_used"] == 1
    assert update["reflection_tool"] == "needs_count"
    assert route_after_tool_result({**state, **update}) == "continue"
    content = json.loads(update["messages"][0].content)
    assert content["reflection"] == {
        "action": "repair_arguments",
        "round": 1,
        "max_rounds": 1,
        "instruction": (
            "Correct the tool name or arguments once using the public tool schema. "
            "Do not repeat unchanged arguments and do not invent hidden error details."
        ),
    }
    assert events == [
        {
            "event": "reflection.started",
            "agent": "parser",
            "tool": "needs_count",
            "error_code": "validation_error",
            "reflection_round": 1,
            "max_rounds": 1,
            "error_count": 1,
        }
    ]


def test_second_validation_error_requires_final_output_instead_of_another_reflection():
    state = {
        "messages": [],
        "dialog_state": ["parser"],
        "reflection_rounds_used": 1,
    }

    update = apply_reflection_policy(
        state,
        {"messages": [_error_message("validation_error")]},
        ReflectionPolicy(max_rounds=1),
    )

    assert update["reflection_status"] == "finalizing"
    assert update["reflection_rounds_used"] == 1
    assert update["reflection_terminal_reason"] == "max_rounds_exhausted"
    assert route_after_tool_result({**state, **update}) == "continue"
    content = json.loads(update["messages"][0].content)
    assert content["reflection"]["action"] == "finalize_without_tools"
    assert content["reflection"]["reason"] == "max_rounds_exhausted"


def test_transport_error_skips_reflection_and_requires_tool_free_finalization():
    state = {
        "messages": [],
        "dialog_state": ["parser"],
        "reflection_rounds_used": 0,
    }

    update = apply_reflection_policy(
        state,
        {
            "messages": [
                _error_message(
                    "dependency_timeout",
                    retryable=True,
                    content="private endpoint and stack trace",
                )
            ]
        },
        ReflectionPolicy(max_rounds=1),
    )

    assert update["reflection_status"] == "finalizing"
    assert update["reflection_rounds_used"] == 0
    assert update["reflection_terminal_reason"] == "non_repairable_error"
    assert "private endpoint" not in update["messages"][0].content
    assert (
        json.loads(update["messages"][0].content)["reflection"]["action"]
        == "finalize_without_tools"
    )


def test_successful_tool_result_resets_active_reflection_without_erasing_usage():
    state = {
        "messages": [],
        "reflection_rounds_used": 1,
        "reflection_status": "repairing",
        "reflection_tool": "needs_count",
        "reflection_error_code": "validation_error",
    }
    message = ToolMessage(content="3", tool_call_id="call-success")

    update = apply_reflection_policy(
        state,
        {"messages": [message]},
        ReflectionPolicy(max_rounds=1),
    )

    assert update["reflection_status"] == "idle"
    assert update["reflection_tool"] == ""
    assert "reflection_rounds_used" not in update
    assert state["reflection_rounds_used"] == 1
    assert route_after_tool_result({**state, **update}) == "continue"


def test_validation_repair_context_contains_schema_location_but_not_input_value():
    class Arguments(BaseModel):
        count: int

    try:
        Arguments.model_validate({"count": "private-input-value"})
    except PydanticValidationError as exc:
        context = safe_validation_repair_context(exc)
    else:
        raise AssertionError("fixture must fail validation")

    assert context == {
        "validation_issues": [
            {
                "location": ["count"],
                "type": "int_parsing",
            }
        ]
    }
    assert "private-input-value" not in json.dumps(context)


def test_real_tool_node_exposes_safe_repair_context_then_enforces_global_limit():
    node = create_tool_node_with_fallback(
        [needs_count],
        TOOL_POLICY,
        ReflectionPolicy(max_rounds=1),
    )
    first_call = AIMessage(
        content="",
        name="parser",
        tool_calls=[
            {
                "name": "needs_count",
                "args": {"count": "private-invalid-count"},
                "id": "call-1",
            }
        ],
    )
    first = node.invoke(
        {
            "messages": [first_call],
            "dialog_state": ["parser"],
            "reflection_rounds_used": 0,
        }
    )

    first_message = first["messages"][0]
    assert first["reflection_status"] == "repairing"
    assert first["reflection_rounds_used"] == 1
    assert first_message.artifact["repair_context"]["validation_issues"][0]["location"] == [
        "count"
    ]
    assert "private-invalid-count" not in first_message.content

    second_call = AIMessage(
        content="",
        name="parser",
        tool_calls=[
            {
                "name": "needs_count",
                "args": {"count": "another-private-value"},
                "id": "call-2",
            }
        ],
    )
    second = node.invoke(
        {
            "messages": [first_call, first_message, second_call],
            "dialog_state": ["parser"],
            "reflection_rounds_used": 1,
            "reflection_status": "repairing",
        }
    )

    assert second["reflection_status"] == "finalizing"
    assert second["reflection_terminal_reason"] == "max_rounds_exhausted"
    assert route_after_tool_result(second) == "continue"
    assert "another-private-value" not in second["messages"][0].content


def test_new_user_request_resets_reflection_usage_but_primary_failure_only_resets_active_state():
    user_info_node = create_user_info_node(lambda **kwargs: "profile")
    request_update = user_info_node(
        {
            "messages": [],
            "user_id": "user-1",
            "namespace": "tech_docs",
            "reflection_rounds_used": 1,
            "reflection_status": "terminal",
        },
        None,
    )

    assert request_update["reflection_rounds_used"] == 0
    assert request_update["reflection_status"] == "idle"

    failure_node = create_primary_tool_failure_node()
    failure_update = failure_node(
        {
            "messages": [],
            "reflection_rounds_used": 1,
            "reflection_status": "terminal",
            "reflection_tool": "needs_count",
            "reflection_error_code": "validation_error",
            "reflection_terminal_reason": "max_rounds_exhausted",
        }
    )

    assert failure_update["messages"][0].name == "primary"
    assert "一次受控修正" in failure_update["messages"][0].content
    assert failure_update["reflection_status"] == "idle"
    assert "reflection_rounds_used" not in failure_update


def test_finalization_router_targets_close_pending_tool_protocol_without_executing_tool():
    pending_call = AIMessage(
        content="",
        name="parser",
        tool_calls=[
            {
                "name": "needs_count",
                "args": {"count": "private-value"},
                "id": "call-closed",
            }
        ],
    )
    state = {
        "messages": [pending_call],
        "dialog_state": ["parser"],
        "reflection_rounds_used": 1,
        "reflection_status": "finalizing",
        "reflection_terminal_reason": "max_rounds_exhausted",
    }

    exit_update = create_exit_node()(state)

    exit_message = exit_update["messages"][0]
    assert exit_message.tool_call_id == "call-closed"
    assert exit_message.status == "error"
    assert exit_message.artifact["error"]["code"] == "reflection_tool_chain_closed"
    assert "private-value" not in exit_message.content
    assert exit_update["reflection_status"] == "idle"

    primary_update = create_primary_tool_failure_node()(state)

    assert primary_update["messages"][0].tool_call_id == "call-closed"
    assert primary_update["messages"][0].status == "error"
    assert primary_update["messages"][1].name == "primary"
    assert primary_update["reflection_status"] == "idle"


def test_assistant_node_resets_active_reflection_only_after_tool_free_output():
    class StubAssistant:
        name = "parser"

        def __init__(self, message):
            self.message = message

        def __call__(self, state, config=None, *, before_llm_attempt=None):
            if before_llm_attempt is not None:
                before_llm_attempt(())
            return {"messages": self.message}

        async def ainvoke(self, state, config=None, *, before_llm_attempt=None):
            return self(state, config, before_llm_attempt=before_llm_attempt)

    state = {
        "messages": [],
        "reflection_rounds_used": 1,
        "reflection_status": "finalizing",
    }
    final_update = assistant_node(StubAssistant(AIMessage(content="partial result"))).invoke(state)

    assert final_update["reflection_status"] == "idle"
    assert "reflection_rounds_used" not in final_update

    tool_update = assistant_node(
        StubAssistant(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "needs_count",
                        "args": {"count": 3},
                        "id": "call-again",
                    }
                ],
            )
        )
    ).invoke(state)

    assert "reflection_status" not in tool_update
    assert tool_update["messages"].tool_calls[0]["name"] == "needs_count"


def test_compiled_tool_result_route_continues_only_for_repairable_error():
    builder = StateGraph(State)
    builder.add_node(
        "tools",
        create_tool_node_with_fallback(
            [needs_count],
            TOOL_POLICY,
            ReflectionPolicy(max_rounds=1),
        ),
    )
    builder.add_node(
        "continued",
        lambda state: {"messages": [AIMessage(content="repair path", name="parser")]},
    )
    builder.add_node(
        "terminated",
        lambda state: {"messages": [AIMessage(content="terminal path", name="primary")]},
    )
    builder.add_edge(START, "tools")
    builder.add_conditional_edges(
        "tools",
        route_after_tool_result,
        {"continue": "continued", "terminate": "terminated"},
    )
    builder.add_edge("continued", END)
    builder.add_edge("terminated", END)
    graph = builder.compile()

    result = graph.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    name="parser",
                    tool_calls=[
                        {
                            "name": "needs_count",
                            "args": {"count": "invalid"},
                            "id": "call-validation",
                        }
                    ],
                )
            ],
            "dialog_state": ["parser"],
            "reflection_rounds_used": 0,
        }
    )

    assert result["messages"][-1].content == "repair path"
    assert result["reflection_status"] == "repairing"
    assert result["reflection_rounds_used"] == 1


def test_compiled_tool_result_route_skips_reflection_but_allows_one_partial_result():
    builder = StateGraph(State)
    builder.add_node(
        "tools",
        create_tool_node_with_fallback(
            [always_times_out],
            TOOL_POLICY,
            ReflectionPolicy(max_rounds=1),
        ),
    )
    builder.add_node(
        "continued",
        lambda state: {"messages": [AIMessage(content="partial result", name="primary")]},
    )
    builder.add_node("terminated", create_primary_tool_failure_node())
    builder.add_edge(START, "tools")
    builder.add_conditional_edges(
        "tools",
        route_after_tool_result,
        {"continue": "continued", "terminate": "terminated"},
    )
    builder.add_edge("continued", END)
    builder.add_edge("terminated", END)
    graph = builder.compile()

    result = graph.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    name="primary",
                    tool_calls=[
                        {
                            "name": "always_times_out",
                            "args": {"query": "private-query"},
                            "id": "call-timeout",
                        }
                    ],
                )
            ],
            "dialog_state": [],
            "reflection_rounds_used": 0,
        }
    )

    assert result["messages"][-1].name == "primary"
    assert result["messages"][-1].content == "partial result"
    assert "private-query" not in result["messages"][-1].content
    assert result["reflection_status"] == "finalizing"
    assert result["reflection_rounds_used"] == 0
