from tech_doc_agent.app.services.assistants.primary_assistant import (
    primary_assistant_sensitive_tools,
    primary_assistant_tools,
)


def test_primary_assistant_has_user_profile_tools():
    safe_tool_names = [_tool_name(tool) for tool in primary_assistant_tools]
    sensitive_tool_names = [_tool_name(tool) for tool in primary_assistant_sensitive_tools]

    assert "web_search" not in safe_tool_names
    assert "read_user_profile" in safe_tool_names
    assert "read_all_learning_history" in safe_tool_names
    assert "read_user_memory" in safe_tool_names
    assert "update_user_profile" in sensitive_tool_names


def _tool_name(tool) -> str:
    return getattr(tool, "name", getattr(tool, "__name__", ""))
