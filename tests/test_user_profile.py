import json
from types import SimpleNamespace

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.resources import override_app_resources
from tech_doc_agent.app.services.user_profile import (
    get_user_context_summary,
    get_user_profile,
    get_user_profile_summary,
    update_user_profile,
)


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
    profile_dir = tmp_path / "user_profiles" / "user-a"
    profile_dir.mkdir(parents=True)
    (profile_dir / "tenant-docs.json").write_text(
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


def test_user_profile_update_persists_structured_profile(tmp_path):
    settings = Settings(DATA_PATH=str(tmp_path))

    updated = update_user_profile(
        user_id="user-a",
        namespace="tenant-docs",
        experience_level="进阶",
        explanation_style="先看工程实现，再补原理",
        known_topics=["LangGraph StateGraph", "Reducer"],
        weak_topics=["Checkpoint", "Reducer"],
        resolved_weak_topics=["Reducer"],
        evidence="根据最近学习记录和用户主动请求更新。",
        settings=settings,
    )
    loaded = get_user_profile("user-a", "tenant-docs", settings=settings)

    assert updated["status"] == "updated"
    assert loaded["experience_level"] == "进阶"
    assert loaded["explanation_style"] == "先看工程实现，再补原理"
    assert loaded["known_topics"] == ["LangGraph StateGraph", "Reducer"]
    assert loaded["weak_topics"] == ["Checkpoint"]
    assert loaded["last_update_reason"] == "根据最近学习记录和用户主动请求更新。"


def test_user_profiles_are_isolated_by_namespace(tmp_path):
    settings = Settings(DATA_PATH=str(tmp_path))

    update_user_profile(
        user_id="user-a",
        namespace="namespace-a",
        experience_level="进阶",
        settings=settings,
    )
    update_user_profile(
        user_id="user-a",
        namespace="namespace-b",
        experience_level="专家",
        settings=settings,
    )

    assert get_user_profile("user-a", "namespace-a", settings=settings)["experience_level"] == "进阶"
    assert get_user_profile("user-a", "namespace-b", settings=settings)["experience_level"] == "专家"


def test_legacy_profile_is_only_visible_in_default_namespace(tmp_path):
    profile_dir = tmp_path / "user_profiles"
    profile_dir.mkdir()
    (profile_dir / "user-a.json").write_text(
        json.dumps({"experience_level": "进阶"}),
        encoding="utf-8",
    )
    settings = Settings(DATA_PATH=str(tmp_path))

    default_profile = get_user_profile("user-a", "tech_docs", settings=settings)
    other_profile = get_user_profile("user-a", "tenant-docs", settings=settings)

    assert default_profile["experience_level"] == "进阶"
    assert other_profile["experience_level"] == "初学者"

    update_user_profile(
        user_id="user-a",
        namespace="tech_docs",
        depth="简洁",
        settings=settings,
    )

    assert (profile_dir / "user-a" / "tech_docs.json").exists()


def test_profile_path_escapes_tenant_values_for_windows(tmp_path):
    settings = Settings(DATA_PATH=str(tmp_path))

    update_user_profile(
        user_id="user:a",
        namespace="docs:private",
        experience_level="进阶",
        settings=settings,
    )

    assert (tmp_path / "user_profiles" / "user%3Aa" / "docs%3Aprivate.json").exists()


def test_user_profile_summary_includes_long_term_profile_fields(tmp_path):
    settings = Settings(DATA_PATH=str(tmp_path))
    update_user_profile(
        user_id="user-a",
        namespace="tenant-docs",
        known_topics=["StateGraph"],
        weak_topics=["Checkpoint"],
        notes="用户希望解释时减少基础铺垫。",
        settings=settings,
    )

    summary = get_user_profile_summary(
        user_id="user-a",
        namespace="tenant-docs",
        settings=settings,
    )

    assert "长期用户画像" in summary
    assert "已掌握/熟悉主题：StateGraph" in summary
    assert "仍需巩固主题：Checkpoint" in summary
    assert "用户希望解释时减少基础铺垫" in summary


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
