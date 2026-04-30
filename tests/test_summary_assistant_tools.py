from tech_doc_agent.app.services.assistants.summary_assistant import (
    summary_assistant_safe_tools,
    summary_assistant_sensitive_tools,
)


def test_summary_assistant_uses_single_combined_sensitive_learning_state_tool():
    assert [tool.name for tool in summary_assistant_safe_tools] == [
        "read_learning_history",
        "read_user_memory",
    ]
    assert [tool.name for tool in summary_assistant_sensitive_tools] == ["upsert_learning_state"]
