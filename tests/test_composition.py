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
        MAX_REFLECTION_ROUNDS=2,
        REQUEST_MAX_SECONDS=12,
        WORKFLOW_MAX_LLM_CALLS=7,
        WORKFLOW_MAX_TOOL_CALLS=8,
        WORKFLOW_MAX_TOTAL_TOKENS=900,
        WORKFLOW_MAX_ESTIMATED_COST_USD="1.5",
        CONTEXT_COMPACTION_MAX_MESSAGES=50,
        CONTEXT_COMPACTION_MAX_SERIALIZED_BYTES=100_000,
        CONTEXT_COMPACTION_KEEP_RECENT_TURNS=3,
        CONTEXT_SUMMARY_MAX_CHARS=8_000,
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
    assert spec_a.execution_policy.tools.max_identical_repeats == 4
    assert spec_a.execution_policy.tools.parser_max_retrieval_calls == 9
    assert spec_a.execution_policy.reflection.max_rounds == 2
    assert spec_a.execution_policy.budget.request_max_seconds == 12
    assert spec_a.execution_policy.budget.workflow_max_llm_calls == 7
    assert spec_a.execution_policy.budget.workflow_max_tool_calls == 8
    assert spec_a.execution_policy.budget.workflow_max_total_tokens == 900
    assert str(
        spec_a.execution_policy.budget.workflow_max_estimated_cost_usd
    ) == "1.5"
    assert spec_a.budget_tracker.execution_budget is spec_a.execution_policy.budget
    assert spec_a.context_compactor.policy.max_messages == 50
    assert spec_a.context_compactor.policy.max_serialized_bytes == 100_000
    assert spec_a.context_compactor.policy.keep_recent_turns == 3
    assert spec_a.context_compactor.policy.summary_max_chars == 8_000
    assert spec_b.context_compactor.policy.enabled is False
    assert spec_b.execution_policy.tools.max_identical_repeats == 2
    assert spec_b.execution_policy.tools.parser_max_retrieval_calls == 6
    assert spec_b.execution_policy.reflection.max_rounds == 1
    assert spec_a.budget_tracker.price_table is resources_a.model_price_table
    assert spec_b.budget_tracker.price_table is resources_b.model_price_table

    graph = build_application_graph(MemorySaver(), resources_a)

    assert graph is not None
    assert set(graph.interrupt_before_nodes) == {
        "parser_assistant_sensitive_tools",
        "examination_assistant_sensitive_tools",
        "summary_assistant_sensitive_tools",
        "primary_assistant_sensitive_tools",
    }
