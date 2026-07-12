from langgraph.checkpoint.memory import MemorySaver

from tech_doc_agent.app.composition import build_application_graph, build_graph_spec
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.resources import AppResources


def test_production_graph_composition_is_offline_and_resource_scoped(tmp_path):
    settings_a = Settings(
        DATA_PATH=str(tmp_path / "a"),
        SEED_DOC_STORE_ON_EMPTY=False,
        MAX_IDENTICAL_TOOL_REPEATS=4,
        PARSER_MAX_RETRIEVAL_CALLS=9,
    )
    settings_b = Settings(DATA_PATH=str(tmp_path / "b"), SEED_DOC_STORE_ON_EMPTY=False)
    resources_a = AppResources.create(settings_a)
    resources_b = AppResources.create(settings_b)

    spec_a = build_graph_spec(resources_a)
    spec_b = build_graph_spec(resources_b)
    parser_a = next(spec for spec in spec_a.subagents if spec.key == "parser")
    parser_b = next(spec for spec in spec_b.subagents if spec.key == "parser")

    assert [tool.name for tool in parser_a.tools.safe] == ["read_docs", "web_search"]
    assert parser_a.tools.safe[0] is not parser_b.tools.safe[0]
    assert parser_a.tools.safe[0].invoke({"query": "StateGraph"}) == "[]"
    assert spec_a.tool_execution_policy.max_identical_repeats == 4
    assert spec_a.tool_execution_policy.parser_max_retrieval_calls == 9
    assert spec_b.tool_execution_policy.max_identical_repeats == 2
    assert spec_b.tool_execution_policy.parser_max_retrieval_calls == 6

    graph = build_application_graph(MemorySaver(), resources_a)

    assert graph is not None
    assert set(graph.interrupt_before_nodes) == {
        "parser_assistant_sensitive_tools",
        "examination_assistant_sensitive_tools",
        "summary_assistant_sensitive_tools",
        "primary_assistant_sensitive_tools",
    }
