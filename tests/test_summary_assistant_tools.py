def test_summary_assistant_uses_single_combined_sensitive_learning_state_tool(graph_spec):
    summary = next(spec for spec in graph_spec.subagents if spec.key == "summary")

    assert [tool.name for tool in summary.tools.safe] == [
        "read_learning_history",
        "read_user_memory",
    ]
    assert [tool.name for tool in summary.tools.sensitive] == ["upsert_learning_state"]
