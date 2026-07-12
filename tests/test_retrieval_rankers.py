import pytest

from tech_doc_agent.app.core.errors import Timeout
from tech_doc_agent.app.infrastructure.retrieval.models import IndexedDocument
from tech_doc_agent.app.infrastructure.retrieval.semantic import SemanticRanker, semantic_score


class ChunkStore:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def search_related(self, query: str, k: int):
        self.calls.append((query, k))
        return self.chunks[:k]


def test_semantic_ranker_deduplicates_document_chunks_and_keeps_unknown_fallback():
    document = _document("known", "Known")
    store = ChunkStore(
        [
            {
                "doc_id": "known",
                "title": "Known",
                "chunk_text": "first",
                "chunk_index": 0,
                "distance": 0.2,
            },
            {
                "doc_id": "known",
                "title": "Known",
                "chunk_text": "duplicate",
                "chunk_index": 1,
                "distance": 0.1,
            },
            {
                "doc_id": "unknown",
                "title": "Unknown",
                "chunk_text": "fallback",
                "chunk_index": 0,
            },
        ]
    )

    ranked = SemanticRanker(store).rank(
        "query",
        [document],
        top_k=3,
        filters={},
    )

    assert [candidate.document.title for candidate in ranked] == ["Known", "Unknown"]
    assert ranked[0].metadata["chunk_text"] == "first"
    assert ranked[0].score == 1 / 1.2
    assert ranked[1].score == 1 / 3
    assert store.calls == [("query", 3)]


def test_semantic_ranker_overfetches_for_filters_and_rejects_unknown_fallback():
    document = _document("known", "Known", category="langgraph_core")
    store = ChunkStore(
        [
            {"doc_id": "unknown", "title": "Unknown", "chunk_text": "skip"},
            {"doc_id": "known", "title": "Known", "chunk_text": "keep"},
        ]
    )

    ranked = SemanticRanker(store).rank(
        "query",
        [document],
        top_k=2,
        filters={"category": "langgraph_core"},
    )

    assert [candidate.document.title for candidate in ranked] == ["Known"]
    assert store.calls == [("query", 10)]


def test_semantic_ranker_preserves_dependency_failure_as_empty_degradation(monkeypatch):
    events = []

    class FailingStore:
        def search_related(self, query: str, k: int):
            raise Timeout(
                dependency="embedding",
                tool="search_related_docs",
                cause_type="ProviderTimeout",
            )

    monkeypatch.setattr(
        "tech_doc_agent.app.infrastructure.retrieval.semantic.log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    ranked = SemanticRanker(FailingStore()).rank(
        "query",
        [_document("known", "Known")],
        top_k=3,
        filters={},
    )

    assert ranked == []
    assert events == [
        (
            "retrieval.semantic.skipped",
            {
                "error_code": "dependency_timeout",
                "retryable": True,
                "safe_message": "A dependency timed out. Try again.",
                "dependency": "embedding",
                "tool": "search_related_docs",
                "cause_type": "ProviderTimeout",
            },
        )
    ]


def test_semantic_ranker_propagates_typed_failure_when_degradation_is_disabled():
    class FailingStore:
        def search_related(self, query: str, k: int):
            raise Timeout(dependency="embedding", cause_type="ProviderTimeout")

    with pytest.raises(Timeout) as exc_info:
        SemanticRanker(FailingStore()).rank(
            "query",
            [_document("known", "Known")],
            top_k=3,
            filters={},
            degrade_on_failure=False,
        )

    assert exc_info.value.dependency == "embedding"


def test_semantic_score_uses_distance_then_rank_fallback():
    assert semantic_score({"distance": 0.25}, rank=4) == 0.8
    assert semantic_score({"distance": -1}, rank=4) == 1.0
    assert semantic_score({}, rank=4) == 0.25


def _document(
    doc_id: str,
    title: str,
    *,
    category: str = "uncategorized",
) -> IndexedDocument:
    metadata = {"category": category, "tags": []}
    raw = {
        "id": doc_id,
        "title": title,
        "content": title,
        "source": "test",
        "metadata": metadata,
    }
    return IndexedDocument(
        key=f"id:{doc_id}",
        doc_id=doc_id,
        title=title,
        content=title,
        source="test",
        metadata=metadata,
        raw=raw,
    )
