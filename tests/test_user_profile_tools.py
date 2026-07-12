import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tech_doc_agent.app.api.routes.learning import router
from tech_doc_agent.app.application.profile_service import (
    UserProfileService as ApplicationUserProfileService,
)
from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.infrastructure.persistence.user_profile_repository import (
    JsonUserProfileRepository,
)
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


def test_profile_tool_update_is_visible_only_to_matching_tenant_api(tmp_path):
    profile_service = ApplicationUserProfileService(
        repository=JsonUserProfileRepository(tmp_path)
    )
    tools = build_tool_bundle(
        ToolDependencies(
            document_store=None,
            document_retriever=None,
            learning_store=None,
            memory_store=None,
            learning_state_service=None,
            profile_service=profile_service,
            web_search=None,
        )
    )
    with trace_context(user_id="user-a", namespace="namespace-a"):
        updated = json.loads(
            tools.update_user_profile.invoke(
                {
                    "experience_level": "进阶",
                    "known_topics": ["StateGraph"],
                    "evidence": "tenant e2e",
                }
            )
        )

    app = FastAPI()
    app.include_router(router)
    app.state.runtime = SimpleNamespace(
        resources=SimpleNamespace(profile_service=profile_service)
    )
    client = TestClient(app)
    namespace_a = client.get(
        "/learning/profile",
        params={"user_id": "user-a", "namespace": "namespace-a"},
    )
    namespace_b = client.get(
        "/learning/profile",
        params={"user_id": "user-a", "namespace": "namespace-b"},
    )

    assert updated["status"] == "updated"
    assert namespace_a.status_code == 200
    assert namespace_a.json()["experience_level"] == "进阶"
    assert namespace_a.json()["known_topics"] == ["StateGraph"]
    assert namespace_b.status_code == 200
    assert namespace_b.json()["experience_level"] == "初学者"
    assert namespace_b.json()["known_topics"] == []
