import json
from types import SimpleNamespace

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
    upsert_learning_history,
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
            }
        ]
        self.saved = False

    def read_by_query(self, query: str):
        return [record for record in self.records if query in record["knowledge"]]

    def read_overview(self):
        return [dict(record) for record in self.records]

    def upsert_record(self, knowledge: str, timestamp: str, score: float | None = None):
        self.records.append(
            {
                "knowledge": knowledge,
                "timestamp": timestamp,
                "score": score or 0.0,
                "reviewtimes": 1,
            }
        )
        return "ok"

    def save(self):
        self.saved = True
        return True


def test_learning_tools_use_resource_registry():
    learning_store = FakeLearningStore()
    test_resources = SimpleNamespace(
        faiss_store=None,
        learning_store=learning_store,
        web_search_backend=None,
    )

    with override_app_resources(test_resources):
        assert json.loads(read_learning_history.invoke({"query": "LangGraph"}))
        assert json.loads(read_all_learning_history.invoke({}))
        assert upsert_learning_history.invoke(
            {
                "knowledge": "FastAPI Depends",
                "timestamp": "2026-04-28T00:00:00Z",
                "score": 0.9,
            }
        ) == "ok"

    assert learning_store.saved is True
    assert learning_store.records[-1]["knowledge"] == "FastAPI Depends"
