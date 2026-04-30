import json
from types import SimpleNamespace

from tech_doc_agent.app.core.observability import trace_context
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.resources import (
    AppResources,
    get_app_resources,
    override_app_resources,
    reset_app_resources,
)
from tech_doc_agent.app.services.tools.learning_store import (
    read_all_learning_history,
    read_learning_history,
    read_user_memory,
    upsert_learning_history,
    upsert_learning_state,
)


def test_app_resources_seeds_stores_in_configured_data_path(tmp_path, monkeypatch):
    def fake_generate_embedding(content):
        if isinstance(content, str):
            return [1.0, 0.0, 0.0]
        return [[float(index + 1), 0.0, 0.0] for index, _ in enumerate(content)]

    monkeypatch.setattr(
        "tech_doc_agent.app.services.vectordb.faiss_store.generate_embedding",
        fake_generate_embedding,
    )
    settings = Settings(
        DATA_PATH=str(tmp_path),
        EMBEDDING_API_KEY="embedding-key",
        EMBEDDING_MODEL="embedding-model",
        SEED_DOC_STORE_ON_EMPTY=True,
    )

    resources = AppResources.create(settings)

    try:
        assert resources.faiss_store.store_dir == tmp_path / "faiss_store"
        assert resources.faiss_store.read_documents("StateGraph")
        assert resources.hybrid_retriever.search("StateGraph")
        assert resources.learning_store.records
        assert resources.memory_store.memories == []
        assert resources.web_search_backend.store_dir == tmp_path / "web_search"
    finally:
        reset_app_resources()


def test_app_resources_skips_faiss_index_when_embedding_is_not_configured(tmp_path):
    settings = Settings(
        DATA_PATH=str(tmp_path),
        EMBEDDING_API_KEY="",
        EMBEDDING_MODEL="",
        SEED_DOC_STORE_ON_EMPTY=True,
    )

    resources = AppResources.create(settings)

    try:
        assert resources.faiss_store.index is None
        assert resources.faiss_store.read_documents("StateGraph")
        assert resources.hybrid_retriever.search("StateGraph")
    finally:
        reset_app_resources()


def test_app_resources_keeps_document_store_empty_when_seed_is_disabled(tmp_path):
    settings = Settings(DATA_PATH=str(tmp_path), SEED_DOC_STORE_ON_EMPTY=False)

    resources = AppResources.create(settings)

    try:
        assert resources.faiss_store.index is None
        assert resources.faiss_store.documents == []
        assert resources.hybrid_retriever.search("StateGraph") == []
    finally:
        reset_app_resources()


def test_override_app_resources_restores_previous_resources():
    first = SimpleNamespace(value="first")
    second = SimpleNamespace(value="second")

    with override_app_resources(first):
        assert get_app_resources().value == "first"
        with override_app_resources(second):
            assert get_app_resources().value == "second"
        assert get_app_resources().value == "first"

    reset_app_resources()


class FakeLearningStore:
    def __init__(self):
        self.records = [
            {
                "knowledge": "LangGraph StateGraph",
                "timestamp": "2024-01-01T10:00:00Z",
                "score": 0.8,
                "reviewtimes": 1,
                "user_id": "default",
                "namespace": "tech_docs",
            }
        ]
        self.saved = False

    def read_by_query(
        self,
        query: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        return [
            record
            for record in self.records
            if query in record["knowledge"]
            and record.get("user_id") == user_id
            and record.get("namespace") == namespace
        ]

    def read_overview(
        self,
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        return [
            dict(record)
            for record in self.records
            if record.get("user_id") == user_id and record.get("namespace") == namespace
        ]

    def upsert_record(
        self,
        knowledge: str,
        timestamp: str,
        score: float | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        self.records.append(
            {
                "knowledge": knowledge,
                "timestamp": timestamp,
                "score": score or 0.0,
                "reviewtimes": 1,
                "user_id": user_id,
                "namespace": namespace,
            }
        )
        return "ok"

    def save(self):
        self.saved = True
        return True


class FakeMemoryStore:
    def __init__(self):
        self.memories = [
            {
                "id": "memory-1",
                "kind": "stuck_point",
                "topic": "LangGraph StateGraph",
                "content": "用户曾经卡在 reducer 和普通覆盖更新的区别。",
                "confidence": 0.8,
                "source_session_id": "session-1",
                "created_at": "2026-04-28T00:00:00+00:00",
                "updated_at": "2026-04-28T00:00:00+00:00",
                "user_id": "default",
                "namespace": "tech_docs",
            }
        ]
        self.saved = False

    def read_by_query(
        self,
        query: str = "",
        user_id: str | None = None,
        namespace: str | None = None,
        limit: int = 5,
    ):
        return [
            dict(memory)
            for memory in self.memories
            if memory.get("user_id") == user_id
            and memory.get("namespace") == namespace
            and (not query or query in memory.get("topic", "") or query in memory.get("content", ""))
        ][:limit]

    def upsert_memory(
        self,
        *,
        kind: str,
        topic: str,
        content: str,
        confidence: float | None = None,
        source_session_id: str | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        memory = {
            "id": f"memory-{len(self.memories) + 1}",
            "kind": kind,
            "topic": topic,
            "content": content,
            "confidence": confidence or 0.7,
            "source_session_id": source_session_id,
            "created_at": "2026-04-28T00:00:00+00:00",
            "updated_at": "2026-04-28T00:00:00+00:00",
            "user_id": user_id,
            "namespace": namespace,
        }
        self.memories.append(memory)
        return memory

    def save(self):
        self.saved = True
        return True


def test_learning_tools_use_resource_registry():
    learning_store = FakeLearningStore()
    memory_store = FakeMemoryStore()
    test_resources = SimpleNamespace(
        faiss_store=None,
        learning_store=learning_store,
        memory_store=memory_store,
        web_search_backend=None,
    )

    with override_app_resources(test_resources):
        assert json.loads(read_learning_history.invoke({"query": "LangGraph"}))
        assert json.loads(read_all_learning_history.invoke({}))
        assert json.loads(read_user_memory.invoke({"query": "StateGraph"}))
        assert upsert_learning_history.invoke(
            {
                "knowledge": "FastAPI Depends",
                "timestamp": "2026-04-28T00:00:00Z",
                "score": 0.9,
            }
        ) == "ok"
        assert "Memory" in upsert_learning_state.invoke(
            {
                "knowledge": "LangGraph StateGraph",
                "timestamp": "2026-04-28T00:00:00Z",
                "score": 0.85,
                "memory_kind": "stuck_point",
                "memory_topic": "LangGraph StateGraph",
                "memory_content": "用户需要继续区分 reducer 和覆盖更新。",
                "memory_confidence": 0.8,
            }
        )

    assert learning_store.saved is True
    assert memory_store.saved is True
    fastapi_record = next(record for record in learning_store.records if record["knowledge"] == "FastAPI Depends")
    assert fastapi_record["user_id"] == "default"
    assert fastapi_record["namespace"] == "tech_docs"
    assert memory_store.memories[-1]["user_id"] == "default"
    assert memory_store.memories[-1]["namespace"] == "tech_docs"


def test_learning_tools_use_trace_context_tenant():
    learning_store = FakeLearningStore()
    memory_store = FakeMemoryStore()
    learning_store.records.append(
        {
            "knowledge": "Tenant Only",
            "timestamp": "2026-04-28T00:00:00Z",
            "score": 0.7,
            "reviewtimes": 1,
            "user_id": "user-a",
            "namespace": "tenant-docs",
        }
    )
    test_resources = SimpleNamespace(
        faiss_store=None,
        learning_store=learning_store,
        memory_store=memory_store,
        web_search_backend=None,
    )

    with override_app_resources(test_resources):
        with trace_context(user_id="user-a", namespace="tenant-docs"):
            assert json.loads(read_learning_history.invoke({"query": "Tenant"}))[0]["knowledge"] == "Tenant Only"
            assert json.loads(read_all_learning_history.invoke({}))[0]["user_id"] == "user-a"
            assert upsert_learning_history.invoke(
                {
                    "knowledge": "Tenant Upsert",
                    "timestamp": "2026-04-28T00:00:00Z",
                    "score": 0.9,
                }
            ) == "ok"

    assert learning_store.records[-1]["user_id"] == "user-a"
    assert learning_store.records[-1]["namespace"] == "tenant-docs"
