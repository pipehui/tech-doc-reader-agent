import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast

import pytest

from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.services.retrieval import HybridRetriever
from tech_doc_agent.app.services.retrieval import hybrid as hybrid_module
from tech_doc_agent.app.services.retrieval.bm25 import BM25Index
from tech_doc_agent.app.services.retrieval.exact import rank_exact
from tech_doc_agent.app.services.retrieval.formatting import format_result
from tech_doc_agent.app.services.retrieval.fusion import reciprocal_rank_fusion
from tech_doc_agent.app.services.retrieval.models import (
    IndexedDocument,
    RankedCandidate,
    RetrievalMode,
    SearchQuery,
    SearchResult,
)
from tech_doc_agent.app.services.retrieval.tokenization import tokenize


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


def _search(
    retriever: HybridRetriever,
    query: str,
    *,
    top_k: int | None = None,
    mode: RetrievalMode = "hybrid",
    filters: dict | None = None,
) -> list[SearchResult]:
    return retriever.retrieve(
        SearchQuery(
            query=query,
            top_k=top_k,
            mode=mode,
            filters=filters or {},
        )
    )


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

    results = _search(retriever, "状态驱动 StateGraph")

    assert results
    assert results[0].title == "LangGraph StateGraph"
    assert "bm25" in results[0].match_types
    assert results[0].score > 0
    assert results[0].source == "seed"


def test_search_compatibility_facade_preserves_dict_contract():
    retriever = HybridRetriever(FakeStore(), settings=_settings())

    typed_results = _search(retriever, "stateful workflows", mode="bm25")
    compatibility_results = retriever.search("stateful workflows", mode="bm25")

    assert compatibility_results == [result.to_dict() for result in typed_results]
    assert isinstance(compatibility_results[0], dict)


def test_hybrid_retriever_fuses_semantic_and_bm25_candidates():
    store = FakeStore()
    retriever = HybridRetriever(store, settings=_settings())

    results = _search(retriever, "graph state resume")

    assert [item.title for item in results[:2]] == ["Checkpoint", "LangGraph StateGraph"]
    assert "semantic" in results[0].match_types
    assert results[0].matched_chunks
    assert store.semantic_queries == [("graph state resume", 5)]


def test_hybrid_retriever_bm25_mode_does_not_call_semantic_search():
    store = FakeStore()
    retriever = HybridRetriever(store, settings=_settings())

    results = _search(retriever, "stateful workflows", mode="bm25")

    assert results
    assert all(item.match_type == "bm25" for item in results)
    assert store.semantic_queries == []


def test_hybrid_retriever_vector_mode_only_returns_semantic_matches():
    store = FakeStore()
    retriever = HybridRetriever(store, settings=_settings())

    results = _search(retriever, "graph state resume", mode="vector")

    assert [item.title for item in results] == ["Checkpoint", "LangGraph StateGraph"]
    assert all(item.match_type == "semantic" for item in results)
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

    results = _search(
        retriever,
        "checkpoint",
        mode="bm25",
        filters={"category": "langgraph_advanced"},
    )

    assert [item.title for item in results] == ["LangGraph Checkpoint Namespace"]
    assert results[0].metadata["category"] == "langgraph_advanced"


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

    results = _search(
        retriever,
        "StateGraph tenant",
        mode="bm25",
        filters={"user_id": "user-a", "namespace": "tenant-docs"},
    )

    assert [item.title for item in results] == ["Tenant A StateGraph"]
    assert results[0].metadata["user_id"] == "user-a"
    assert results[0].metadata["namespace"] == "tenant-docs"


def test_hybrid_retriever_filters_vector_candidates_after_semantic_search():
    store = FakeStore()
    store.documents[0]["metadata"] = {"category": "langgraph_core", "tags": ["stategraph"]}
    store.documents[2]["metadata"] = {"category": "langgraph_advanced", "tags": ["checkpoint"]}
    retriever = HybridRetriever(store, settings=_settings())

    results = _search(
        retriever,
        "graph state resume",
        mode="vector",
        filters={"category": "langgraph_core"},
    )

    assert [item.title for item in results] == ["LangGraph StateGraph"]
    assert all(item.metadata["category"] == "langgraph_core" for item in results)
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

    first_results = _search(retriever, "StateGraph")
    store.documents.append(
        {
            "id": 2,
            "title": "Function Calling",
            "content": "Function Calling maps model tool calls to structured arguments.",
            "source": "seed",
        }
    )
    second_results = _search(retriever, "structured arguments")

    assert first_results[0].title == "LangGraph StateGraph"
    assert second_results[0].title == "Function Calling"


def test_search_query_snapshots_filters_and_rejects_unknown_mode():
    filters = {"category": "langgraph_core"}
    request = SearchQuery(query="StateGraph", filters=filters)
    filters["category"] = "fastapi"

    assert request.filters == {"category": "langgraph_core"}
    with pytest.raises(ValueError, match="Unsupported retrieval mode"):
        SearchQuery(
            query="StateGraph",
            mode=cast(RetrievalMode, "unknown"),
        )


def test_explicit_zero_top_k_short_circuits_before_index_construction():
    retriever = HybridRetriever(FakeStore(), settings=_settings())

    assert _search(retriever, "StateGraph", top_k=0) == []
    assert retriever._index_snapshot is None


def test_parallel_refresh_serializes_bm25_snapshot_publication(monkeypatch):
    retriever = HybridRetriever(FakeStore(), settings=_settings())
    _search(retriever, "StateGraph", mode="bm25")
    original_index = hybrid_module.BM25Index
    guard = threading.Lock()
    active_builds = 0
    max_active_builds = 0

    class CountingIndex(original_index):
        def __init__(self, documents):
            nonlocal active_builds, max_active_builds
            with guard:
                active_builds += 1
                max_active_builds = max(max_active_builds, active_builds)
            try:
                time.sleep(0.01)
                super().__init__(documents)
            finally:
                with guard:
                    active_builds -= 1

    monkeypatch.setattr(hybrid_module, "BM25Index", CountingIndex)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: retriever.refresh(), range(4)))

    assert max_active_builds == 1


def test_failed_refresh_keeps_last_published_snapshot(monkeypatch):
    retriever = HybridRetriever(FakeStore(), settings=_settings())
    _search(retriever, "StateGraph", mode="bm25")
    published_snapshot = retriever._index_snapshot

    class FailingIndex:
        def __init__(self, documents):
            raise RuntimeError("injected BM25 rebuild failure")

    monkeypatch.setattr(hybrid_module, "BM25Index", FailingIndex)

    with pytest.raises(RuntimeError, match="injected BM25 rebuild failure"):
        retriever.refresh()

    assert retriever._index_snapshot is published_snapshot


def test_tokenize_splits_camel_case_and_emits_cjk_unigrams_and_bigrams():
    assert tokenize("StateGraph 检索增强") == [
        "stategraph",
        "state",
        "graph",
        "检",
        "索",
        "增",
        "强",
        "检索",
        "索增",
        "增强",
    ]


def test_bm25_ties_are_stable_by_document_title():
    documents = [
        _indexed_document(key="b", title="Beta", content="shared term"),
        _indexed_document(key="a", title="Alpha", content="shared term"),
    ]

    ranked = BM25Index(documents).search("shared", top_k=2)

    assert [candidate.document.title for candidate in ranked] == ["Alpha", "Beta"]
    assert ranked[0].score == ranked[1].score


def test_exact_rank_prefers_title_and_content_match_then_title_order():
    documents = [
        _indexed_document(key="b", title="StateGraph Beta", content="StateGraph details"),
        _indexed_document(key="a", title="StateGraph Alpha", content="StateGraph details"),
        _indexed_document(key="c", title="Other", content="StateGraph details"),
    ]

    ranked = rank_exact("stategraph", documents)

    assert [(item.document.title, item.score) for item in ranked] == [
        ("StateGraph Alpha", 3.0),
        ("StateGraph Beta", 3.0),
        ("Other", 2.0),
    ]


def test_rrf_combines_signals_and_preserves_semantic_chunk_provenance():
    alpha = _indexed_document(key="alpha", title="Alpha", content="alpha")
    beta = _indexed_document(key="beta", title="Beta", content="beta")
    rankings = {
        "exact": [
            RankedCandidate(key="beta", document=beta, score=2.0),
            RankedCandidate(key="alpha", document=alpha, score=1.0),
        ],
        "semantic": [
            RankedCandidate(
                key="alpha",
                document=alpha,
                score=0.8,
                metadata={"distance": 0.25, "chunk_index": 2, "chunk_text": "matched"},
            )
        ],
    }

    fused = reciprocal_rank_fusion(rankings, rrf_k=60)
    result = format_result(fused[0])

    assert [candidate.document.title for candidate in fused] == ["Alpha", "Beta"]
    assert isinstance(result, SearchResult)
    assert result.match_type == "exact+semantic"
    assert result.signals == {
        "exact": {"rank": 2, "score": 1.0},
        "semantic": {"rank": 1, "score": 0.8, "distance": 0.25, "chunk_index": 2},
    }
    assert result.matched_chunks == (
        {"text": "matched", "chunk_index": 2, "distance": 0.25},
    )
    assert result.to_dict() == {
        "id": "alpha",
        "title": "Alpha",
        "content": "alpha",
        "source": "test",
        "metadata": {"category": "uncategorized", "tags": []},
        "match_type": "exact+semantic",
        "score": 0.032522,
        "retrieval": {
            "score_type": "rrf",
            "signals": {
                "exact": {"rank": 2, "score": 1.0},
                "semantic": {
                    "rank": 1,
                    "score": 0.8,
                    "distance": 0.25,
                    "chunk_index": 2,
                },
            },
        },
        "matched_chunks": [
            {"text": "matched", "chunk_index": 2, "distance": 0.25},
        ],
    }

    fused[0].signals["exact"]["rank"] = 99
    fused[0].matched_chunks[0]["text"] = "changed"

    assert result.signals["exact"]["rank"] == 2
    assert result.matched_chunks[0]["text"] == "matched"


def test_rrf_equal_scores_and_ranks_use_title_as_final_tie_break():
    alpha = _indexed_document(key="alpha", title="Alpha", content="alpha")
    beta = _indexed_document(key="beta", title="Beta", content="beta")

    fused = reciprocal_rank_fusion(
        {
            "bm25": [
                RankedCandidate(key="beta", document=beta, score=1.0),
                RankedCandidate(key="alpha", document=alpha, score=0.5),
            ],
            "semantic": [
                RankedCandidate(key="alpha", document=alpha, score=1.0),
                RankedCandidate(key="beta", document=beta, score=0.5),
            ],
        },
        rrf_k=60,
    )

    assert [candidate.document.title for candidate in fused] == ["Alpha", "Beta"]


def test_hybrid_module_keeps_staged_private_ranker_aliases():
    from tech_doc_agent.app.services.retrieval import hybrid

    assert hybrid.BM25Index is BM25Index
    assert hybrid._tokenize is tokenize
    assert hybrid._rank_exact is rank_exact
    assert hybrid._reciprocal_rank_fusion is reciprocal_rank_fusion
    assert hybrid._format_result is format_result


def _indexed_document(*, key: str, title: str, content: str) -> IndexedDocument:
    return IndexedDocument(
        key=key,
        doc_id=key,
        title=title,
        content=content,
        source="test",
        metadata={"category": "uncategorized", "tags": []},
        raw={"id": key, "title": title, "content": content, "source": "test"},
    )
