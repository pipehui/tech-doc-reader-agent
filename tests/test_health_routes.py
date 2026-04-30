from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

from tech_doc_agent.app.api.routes import health
from tech_doc_agent.app.core.settings import Settings


class FakeRedis:
    closed = False

    def ping(self):
        return True

    def close(self):
        self.closed = True


def _app_with_runtime(runtime=None) -> FastAPI:
    app = FastAPI()
    if runtime is not None:
        app.state.runtime = runtime
    app.include_router(health.router)
    return app


def _ready_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        settings=Settings(REDIS_URL="redis://test"),
        graph=object(),
        checkpointer=object(),
        resources=SimpleNamespace(
            faiss_store=SimpleNamespace(documents=[{"title": "StateGraph"}], index=object()),
            learning_store=SimpleNamespace(records=[{"knowledge": "StateGraph"}]),
            memory_store=SimpleNamespace(memories=[{"topic": "StateGraph"}]),
            hybrid_retriever=object(),
            web_search_backend=object(),
        ),
    )


def test_health_endpoint_returns_ok():
    response = TestClient(_app_with_runtime()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_endpoint_returns_ready_when_dependencies_are_available(monkeypatch):
    monkeypatch.setattr(health.Redis, "from_url", lambda *args, **kwargs: FakeRedis())
    response = TestClient(_app_with_runtime(_ready_runtime())).get("/ready")

    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ready"
    assert {check["name"] for check in payload["checks"]} >= {
        "runtime",
        "graph",
        "checkpointer",
        "resources",
        "faiss_store",
        "hybrid_retriever",
        "learning_store",
        "memory_store",
        "web_search_backend",
        "redis",
    }
    assert all(check["ok"] for check in payload["checks"])


def test_ready_endpoint_returns_503_when_runtime_is_missing():
    response = TestClient(_app_with_runtime()).get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"] == [
        {"name": "runtime", "ok": False, "error": "ChatRuntime is not initialized."}
    ]


def test_ready_endpoint_returns_503_when_redis_is_unavailable(monkeypatch):
    def unavailable_redis(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(health.Redis, "from_url", unavailable_redis)
    response = TestClient(_app_with_runtime(_ready_runtime())).get("/ready")
    payload = response.json()

    assert response.status_code == 503
    assert payload["status"] == "not_ready"
    redis_check = next(check for check in payload["checks"] if check["name"] == "redis")
    assert redis_check["ok"] is False
    assert redis_check["error_type"] == "ConnectionError"
