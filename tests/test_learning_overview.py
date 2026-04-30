from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tech_doc_agent.app.api.routes.learning import router
from tech_doc_agent.app.api.routes.learning import _needs_review
from tech_doc_agent.app.api.schemas import LearningRecord
from tech_doc_agent.app.services.resources import override_app_resources


def test_needs_review_when_score_is_low():
    record = LearningRecord(
        knowledge="LangGraph StateGraph",
        timestamp="2026-04-28T00:00:00Z",
        score=0.4,
        reviewtimes=1,
    )

    assert _needs_review(record, now=record_timestamp("2026-04-28T00:00:00Z"))


def test_needs_review_when_record_is_old():
    record = LearningRecord(
        knowledge="FastAPI Depends",
        timestamp="2026-04-01T00:00:00Z",
        score=0.9,
        reviewtimes=1,
    )

    assert _needs_review(record, now=record_timestamp("2026-04-28T00:00:00Z"))


def record_timestamp(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_learning_routes_filter_by_tenant_query_params():
    class FakeStore:
        def read_overview(self, user_id: str | None = None, namespace: str | None = None):
            records = [
                {
                    "knowledge": "Tenant A",
                    "timestamp": "2026-04-28T00:00:00Z",
                    "score": 0.9,
                    "reviewtimes": 1,
                    "user_id": "user-a",
                    "namespace": "tenant-docs",
                },
                {
                    "knowledge": "Tenant B",
                    "timestamp": "2026-04-28T00:00:00Z",
                    "score": 0.5,
                    "reviewtimes": 1,
                    "user_id": "user-b",
                    "namespace": "tenant-docs",
                },
            ]
            return [
                record
                for record in records
                if record["user_id"] == user_id and record["namespace"] == namespace
            ]

    class FakeMemoryStore:
        def read_by_query(
            self,
            query: str = "",
            user_id: str | None = None,
            namespace: str | None = None,
            limit: int = 20,
        ):
            memories = [
                {
                    "id": "memory-a",
                    "kind": "stuck_point",
                    "topic": "Tenant A",
                    "content": "用户卡在 StateGraph reducer。",
                    "confidence": 0.8,
                    "source_session_id": "session-a",
                    "created_at": "2026-04-28T00:00:00+00:00",
                    "updated_at": "2026-04-28T00:00:00+00:00",
                    "user_id": "user-a",
                    "namespace": "tenant-docs",
                }
            ]
            return [
                memory
                for memory in memories
                if memory["user_id"] == user_id
                and memory["namespace"] == namespace
                and (not query or query in memory["topic"] or query in memory["content"])
            ][:limit]

    app = FastAPI()
    app.include_router(router)
    resources = SimpleNamespace(
        faiss_store=None,
        learning_store=FakeStore(),
        memory_store=FakeMemoryStore(),
        web_search_backend=None,
    )

    with override_app_resources(resources):
        response = TestClient(app).get(
            "/learning/overview",
            params={"user_id": "user-a", "namespace": "tenant-docs"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "user-a"
    assert payload["namespace"] == "tenant-docs"
    assert payload["total"] == 1
    assert payload["records"][0]["knowledge"] == "Tenant A"

    with override_app_resources(resources):
        memory_response = TestClient(app).get(
            "/learning/memory",
            params={"user_id": "user-a", "namespace": "tenant-docs", "query": "StateGraph"},
        )

    assert memory_response.status_code == 200
    memory_payload = memory_response.json()
    assert memory_payload["user_id"] == "user-a"
    assert memory_payload["total"] == 1
    assert memory_payload["memories"][0]["kind"] == "stuck_point"


def test_learning_profile_route_resolves_tenant(monkeypatch):
    def fake_get_user_profile(user_id: str | None = None, namespace: str | None = None):
        return {
            "profile_version": 1,
            "user_id": user_id,
            "namespace": namespace,
            "experience_level": "进阶",
            "explanation_style": "先看工程实现",
            "depth": "中等",
            "language": "中文",
            "known_topics": ["StateGraph"],
            "weak_topics": ["Checkpoint"],
            "notes": "用户主动更新过画像。",
            "last_update_reason": "测试",
            "updated_at": "2026-04-30T00:00:00+00:00",
        }

    monkeypatch.setattr(
        "tech_doc_agent.app.api.routes.learning.get_user_profile",
        fake_get_user_profile,
    )

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(
        "/learning/profile",
        params={"user_id": "user-a", "namespace": "tenant-docs"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "user-a"
    assert payload["namespace"] == "tenant-docs"
    assert payload["known_topics"] == ["StateGraph"]
