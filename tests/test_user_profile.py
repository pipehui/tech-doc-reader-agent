import json
from types import SimpleNamespace

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.resources import override_app_resources
from tech_doc_agent.app.services.user_profile import get_user_context_summary, get_user_profile_summary


def test_user_profile_summary_uses_defaults_when_profile_is_missing(tmp_path):
    summary = get_user_profile_summary(
        user_id="user-a",
        namespace="tenant-docs",
        settings=Settings(DATA_PATH=str(tmp_path)),
    )

    assert "用户ID：user-a" in summary
    assert "知识库命名空间：tenant-docs" in summary
    assert "经验水平：初学者" in summary


def test_user_profile_summary_loads_user_profile_file(tmp_path):
    profile_dir = tmp_path / "user_profiles"
    profile_dir.mkdir()
    (profile_dir / "user-a.json").write_text(
        json.dumps({"experience_level": "进阶", "depth": "简洁"}),
        encoding="utf-8",
    )

    summary = get_user_profile_summary(
        user_id="user-a",
        namespace="tenant-docs",
        settings=Settings(DATA_PATH=str(tmp_path)),
    )

    assert "经验水平：进阶" in summary
    assert "解释深度：简洁" in summary
    assert "语言偏好：中文为主，技术术语保留英文" in summary


def test_user_context_summary_includes_tenant_memory(tmp_path):
    class FakeMemoryStore:
        def read_by_query(self, query: str, user_id: str, namespace: str, limit: int):
            assert query == "StateGraph"
            assert user_id == "user-a"
            assert namespace == "tenant-docs"
            assert limit == 5
            return [
                {
                    "kind": "stuck_point",
                    "topic": "LangGraph StateGraph",
                    "content": "用户容易混淆 reducer 和普通状态覆盖。",
                }
            ]

    resources = SimpleNamespace(memory_store=FakeMemoryStore())

    with override_app_resources(resources):
        summary = get_user_context_summary(
            user_id="user-a",
            namespace="tenant-docs",
            memory_query="StateGraph",
            settings=Settings(DATA_PATH=str(tmp_path)),
        )

    assert "长期学习轨迹记忆" in summary
    assert "[stuck_point] LangGraph StateGraph" in summary
