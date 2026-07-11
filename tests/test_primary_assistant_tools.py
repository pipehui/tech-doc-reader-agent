def test_primary_assistant_has_user_profile_tools(graph_spec):
    safe_tool_names = [_tool_name(tool) for tool in graph_spec.primary.tools.safe]
    sensitive_tool_names = [_tool_name(tool) for tool in graph_spec.primary.tools.sensitive]

    assert "web_search" not in safe_tool_names
    assert "read_user_profile" in safe_tool_names
    assert "read_all_learning_history" in safe_tool_names
    assert "read_user_memory" in safe_tool_names
    assert "update_user_profile" in sensitive_tool_names


def _tool_name(tool) -> str:
    return getattr(tool, "name", getattr(tool, "__name__", ""))
