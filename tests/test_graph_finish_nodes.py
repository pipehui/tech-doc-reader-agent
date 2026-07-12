import pytest
from langchain_core.messages import AIMessage

from tech_doc_agent.app.core.state import update_dialog_stack
from tech_doc_agent.app.graph.nodes import create_finish_node
from tech_doc_agent.app.graph.specs import CompletionPolicy


@pytest.mark.parametrize(
    ("agent", "expected_result_key", "structured"),
    [
        ("parser", "parser_result", True),
        ("relation", "relation_result", True),
        ("explanation", None, False),
        ("examination", "examination_context", False),
        ("summary", None, False),
    ],
)
def test_each_agent_finish_policy_advances_plan_and_writes_only_its_result(
    graph_spec,
    agent,
    expected_result_key,
    structured,
):
    spec = next(spec for spec in graph_spec.subagents if spec.key == agent)
    finish = create_finish_node(spec.completion)
    content = f"{agent} final answer"
    state = {
        "messages": [AIMessage(content=content, name=agent)],
        "dialog_state": ["primary", agent],
        "plan_index": 2,
        "parser_result": {"existing": "parser"},
        "relation_result": {"existing": "relation"},
        "examination_context": "existing examination",
    }

    update = finish(state)

    assert update["dialog_state"] == "pop"
    assert update_dialog_stack(
        state["dialog_state"],
        update["dialog_state"],
    ) == ["primary"]
    assert update["plan_index"] == 3
    for result_key in (
        "parser_result",
        "relation_result",
        "examination_context",
    ):
        assert (result_key in update) is (result_key == expected_result_key)

    if expected_result_key is None:
        return
    if structured:
        assert update[expected_result_key]["raw_text"] == content
        assert isinstance(update[expected_result_key]["parsed"], bool)
    else:
        assert update[expected_result_key] == content


@pytest.mark.parametrize(
    "policy_args",
    [
        {"structured_kind": "parser"},
        {"result_key": "parser_result"},
        {"result_key": "parser_result", "structured_kind": "relation"},
        {"result_key": "relation_result", "structured_kind": "parser"},
        {"result_key": "examination_context", "structured_kind": "parser"},
    ],
)
def test_completion_policy_rejects_semantically_inconsistent_result_contracts(
    policy_args,
):
    with pytest.raises(ValueError, match="supported state result"):
        CompletionPolicy(**policy_args)


def test_finish_policy_defaults_missing_plan_index_to_first_completed_step():
    finish = create_finish_node()

    update = finish(
        {
            "messages": [AIMessage(content="done", name="summary")],
            "dialog_state": ["summary"],
        }
    )

    assert update["plan_index"] == 1
    assert update["dialog_state"] == "pop"
