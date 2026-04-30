from types import SimpleNamespace

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.retrieval import HybridRetriever


class FakeStore:
    def __init__(self):
        self.documents = [
            {
                "id": 1,
                "title": "LangGraph StateGraph",
                "content": "StateGraph builds stateful workflows with nodes and edges.",
                "source": "seed",
            },
            {
                "id": 2,
                "title": "FastAPI Depends",
                "content": "Depends supports dependency injection and request scoped resources.",
                "source": "seed",
            },
            {
                "id": 3,
                "title": "Checkpoint",
                "content": "Checkpoints persist graph state so a workflow can resume later.",
                "source": "seed",
            },
        ]
        self.semantic_queries = []

    def search_related(self, query: str, k: int):
        self.semantic_queries.append((query, k))
        return [
            {
                "doc_id": 3,
                "title": "Checkpoint",
                "chunk_text": "Checkpoints persist graph state.",
                "chunk_index": 0,
                "source": "seed",
                "distance": 0.2,
            },
            {
                "doc_id": 1,
                "title": "LangGraph StateGraph",
                "chunk_text": "StateGraph builds stateful workflows.",
                "chunk_index": 0,
                "source": "seed",
                "distance": 0.4,
            },
        ][:k]


def _settings(**overrides):
    values = {
        "HYBRID_RAG_TOP_K": 5,
        "HYBRID_RAG_BM25_TOP_K": 5,
        "HYBRID_RAG_VECTOR_TOP_K": 5,
        "HYBRID_RAG_RRF_K": 60,
    }
    values.update(overrides)
    return Settings(**values)


def test_hybrid_retriever_returns_bm25_results_without_vector_index():
    store = SimpleNamespace(
        documents=[
            {
                "id": 1,
                "title": "LangGraph StateGraph",
                "content": "StateGraph 是 LangGraph 的核心类，用于构建状态驱动工作流。",
                "source": "seed",
            },
            {
                "id": 2,
                "title": "FastAPI Depends",
                "content": "Depends 实现依赖注入。",
                "source": "seed",
            },
        ]
    )
    retriever = HybridRetriever(store, settings=_settings(HYBRID_RAG_VECTOR_TOP_K=0))

    results = retriever.search("状态驱动 StateGraph")

    assert results
    assert results[0]["title"] == "LangGraph StateGraph"
    assert "bm25" in results[0]["match_type"]
    assert results[0]["score"] > 0
    assert results[0]["source"] == "seed"


def test_hybrid_retriever_fuses_semantic_and_bm25_candidates():
    store = FakeStore()
    retriever = HybridRetriever(store, settings=_settings())

    results = retriever.search("graph state resume")

    assert [item["title"] for item in results[:2]] == ["Checkpoint", "LangGraph StateGraph"]
    assert "semantic" in results[0]["match_type"]
    assert "matched_chunks" in results[0]
    assert store.semantic_queries == [("graph state resume", 5)]


def test_hybrid_retriever_bm25_mode_does_not_call_semantic_search():
    store = FakeStore()
    retriever = HybridRetriever(store, settings=_settings())

    results = retriever.search("stateful workflows", mode="bm25")

    assert results
    assert all(item["match_type"] == "bm25" for item in results)
    assert store.semantic_queries == []


def test_hybrid_retriever_vector_mode_only_returns_semantic_matches():
    store = FakeStore()
    retriever = HybridRetriever(store, settings=_settings())

    results = retriever.search("graph state resume", mode="vector")

    assert [item["title"] for item in results] == ["Checkpoint", "LangGraph StateGraph"]
    assert all(item["match_type"] == "semantic" for item in results)
    assert store.semantic_queries == [("graph state resume", 5)]


def test_hybrid_retriever_filters_bm25_candidates_by_category():
    store = SimpleNamespace(
        documents=[
            {
                "id": 1,
                "title": "LangGraph Checkpoint",
                "content": "checkpoint persists graph state",
                "source": "seed",
                "metadata": {"category": "langgraph_core", "tags": ["checkpoint"]},
            },
            {
                "id": 2,
                "title": "LangGraph Checkpoint Namespace",
                "content": "checkpoint namespace isolates persisted state",
                "source": "seed",
                "metadata": {"category": "langgraph_advanced", "tags": ["checkpoint"]},
            },
        ],
        search_related=lambda query, k: [],
    )
    retriever = HybridRetriever(store, settings=_settings(HYBRID_RAG_VECTOR_TOP_K=0))

    results = retriever.search("checkpoint", mode="bm25", filters={"category": "langgraph_advanced"})

    assert [item["title"] for item in results] == ["LangGraph Checkpoint Namespace"]
    assert results[0]["metadata"]["category"] == "langgraph_advanced"


def test_hybrid_retriever_filters_bm25_candidates_by_user_and_namespace():
    store = SimpleNamespace(
        documents=[
            {
                "id": 1,
                "title": "Tenant A StateGraph",
                "content": "StateGraph tenant scoped content",
                "source": "seed",
                "metadata": {"user_id": "user-a", "namespace": "tenant-docs"},
            },
            {
                "id": 2,
                "title": "Tenant B StateGraph",
                "content": "StateGraph tenant scoped content",
                "source": "seed",
                "metadata": {"user_id": "user-b", "namespace": "tenant-docs"},
            },
        ],
        search_related=lambda query, k: [],
    )
    retriever = HybridRetriever(store, settings=_settings(HYBRID_RAG_VECTOR_TOP_K=0))

    results = retriever.search(
        "StateGraph tenant",
        mode="bm25",
        filters={"user_id": "user-a", "namespace": "tenant-docs"},
    )

    assert [item["title"] for item in results] == ["Tenant A StateGraph"]
    assert results[0]["metadata"]["user_id"] == "user-a"
    assert results[0]["metadata"]["namespace"] == "tenant-docs"


def test_hybrid_retriever_filters_vector_candidates_after_semantic_search():
    store = FakeStore()
    store.documents[0]["metadata"] = {"category": "langgraph_core", "tags": ["stategraph"]}
    store.documents[2]["metadata"] = {"category": "langgraph_advanced", "tags": ["checkpoint"]}
    retriever = HybridRetriever(store, settings=_settings())

    results = retriever.search("graph state resume", mode="vector", filters={"category": "langgraph_core"})

    assert [item["title"] for item in results] == ["LangGraph StateGraph"]
    assert all(item["metadata"]["category"] == "langgraph_core" for item in results)
    assert store.semantic_queries == [("graph state resume", 25)]


def test_hybrid_retriever_rebuilds_bm25_when_documents_change():
    store = SimpleNamespace(
        documents=[
            {
                "id": 1,
                "title": "LangGraph StateGraph",
                "content": "StateGraph workflow",
                "source": "seed",
            }
        ],
        search_related=lambda query, k: [],
    )
    retriever = HybridRetriever(store, settings=_settings(HYBRID_RAG_VECTOR_TOP_K=0))

    first_results = retriever.search("StateGraph")
    store.documents.append(
        {
            "id": 2,
            "title": "Function Calling",
            "content": "Function Calling maps model tool calls to structured arguments.",
            "source": "seed",
        }
    )
    second_results = retriever.search("structured arguments")

    assert first_results[0]["title"] == "LangGraph StateGraph"
    assert second_results[0]["title"] == "Function Calling"
