import json

from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.user_profile import UserProfileService
from tech_doc_agent.app.tools import ToolDependencies, build_tool_bundle


def test_user_profile_tools_use_current_tenant(tmp_path):
    settings = Settings(DATA_PATH=str(tmp_path))
    tools = build_tool_bundle(
        ToolDependencies(
            document_store=None,
            document_retriever=None,
            learning_store=None,
            memory_store=None,
            learning_state_service=None,
            profile_service=UserProfileService(settings),
            web_search=None,
        )
    )

    with trace_context(user_id="user-a", namespace="tenant-docs"):
        initial = json.loads(tools.read_user_profile.invoke({}))
        updated = json.loads(
            tools.update_user_profile.invoke(
                {
                    "experience_level": "进阶",
                    "known_topics": ["LangGraph StateGraph"],
                    "weak_topics": ["Checkpoint"],
                    "evidence": "用户主动要求根据最近学习记录更新能力信息。",
                }
            )
        )
        reloaded = json.loads(tools.read_user_profile.invoke({}))

    assert initial["user_id"] == "user-a"
    assert initial["namespace"] == "tenant-docs"
    assert updated["status"] == "updated"
    assert reloaded["experience_level"] == "进阶"
    assert reloaded["known_topics"] == ["LangGraph StateGraph"]
    assert reloaded["weak_topics"] == ["Checkpoint"]
    assert reloaded["last_update_reason"] == "用户主动要求根据最近学习记录更新能力信息。"
