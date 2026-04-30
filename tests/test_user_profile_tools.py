import json

from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.tools import read_user_profile, update_user_profile


def test_user_profile_tools_use_current_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tech_doc_agent.app.services.tools.user_profile.get_settings",
        lambda: Settings(DATA_PATH=str(tmp_path)),
    )

    with trace_context(user_id="user-a", namespace="tenant-docs"):
        initial = json.loads(read_user_profile.invoke({}))
        updated = json.loads(
            update_user_profile.invoke(
                {
                    "experience_level": "进阶",
                    "known_topics": ["LangGraph StateGraph"],
                    "weak_topics": ["Checkpoint"],
                    "evidence": "用户主动要求根据最近学习记录更新能力信息。",
                }
            )
        )
        reloaded = json.loads(read_user_profile.invoke({}))

    assert initial["user_id"] == "user-a"
    assert initial["namespace"] == "tenant-docs"
    assert updated["status"] == "updated"
    assert reloaded["experience_level"] == "进阶"
    assert reloaded["known_topics"] == ["LangGraph StateGraph"]
    assert reloaded["weak_topics"] == ["Checkpoint"]
    assert reloaded["last_update_reason"] == "用户主动要求根据最近学习记录更新能力信息。"
