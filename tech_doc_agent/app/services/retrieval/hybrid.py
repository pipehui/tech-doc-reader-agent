from __future__ import annotations

from typing import Any

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.services.retrieval.bm25 import BM25Index
from tech_doc_agent.app.services.retrieval.documents import (
    document_key,
    filter_documents,
    metadata_signature,
    normalize_documents,
)
from tech_doc_agent.app.services.retrieval.exact import rank_exact
from tech_doc_agent.app.services.retrieval.filters import normalize_filter
from tech_doc_agent.app.services.retrieval.formatting import (
    MATCH_TYPE_ORDER as MATCH_TYPE_ORDER,
    format_result,
)
from tech_doc_agent.app.services.retrieval.fusion import (
    best_signal_rank,
    clean_signal_metadata,
    reciprocal_rank_fusion,
)
from tech_doc_agent.app.services.retrieval.models import (
    FusedCandidate as FusedCandidate,
    IndexedDocument,
    MetadataFilter,
    RankedCandidate,
    RetrievalStorePort,
    RetrievalMode,
)
from tech_doc_agent.app.services.retrieval.semantic import (
    SemanticRanker,
    document_for_chunk,
    semantic_score,
)
from tech_doc_agent.app.services.retrieval.tokenization import (
    CAMEL_RE as CAMEL_RE,
    TOKEN_RE as TOKEN_RE,
    is_cjk,
    tokenize,
)


class HybridRetriever:
    def __init__(
        self,
        store: RetrievalStorePort,
        *,
        settings: Settings | None = None,
        top_k: int | None = None,
        bm25_top_k: int | None = None,
        vector_top_k: int | None = None,
        rrf_k: int | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self.top_k = top_k or self.settings.HYBRID_RAG_TOP_K
        self.bm25_top_k = bm25_top_k or self.settings.HYBRID_RAG_BM25_TOP_K
        self.vector_top_k = vector_top_k or self.settings.HYBRID_RAG_VECTOR_TOP_K
        self.rrf_k = rrf_k or self.settings.HYBRID_RAG_RRF_K
        self._signature: tuple[tuple[Any, str, str, str, str], ...] | None = None
        self._documents: list[IndexedDocument] = []
        self._bm25_index = BM25Index([])

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        mode: RetrievalMode = "hybrid",
        filters: MetadataFilter | None = None,
    ) -> list[dict[str, Any]]:
        top_k = top_k or self.top_k
        if top_k <= 0:
            return []
        filters = normalize_filter(filters)

        documents = self._ensure_bm25_index()
        if not documents:
            log_event(
                "retrieval.hybrid.finished",
                mode=mode,
                filters=filters,
                result_count=0,
                exact_count=0,
                bm25_count=0,
                semantic_count=0,
            )
            return []

        filtered_documents = filter_documents(documents, filters)
        rankings = self._rankings_for_mode(
            query,
            filtered_documents,
            mode=mode,
            filters=filters,
        )
        fused = reciprocal_rank_fusion(rankings, rrf_k=self.rrf_k)
        results = [format_result(item) for item in fused[:top_k]]
        log_event(
            "retrieval.hybrid.finished",
            mode=mode,
            filters=filters,
            result_count=len(results),
            candidate_documents=len(filtered_documents),
            exact_count=len(rankings.get("exact", [])),
            bm25_count=len(rankings.get("bm25", [])),
            semantic_count=len(rankings.get("semantic", [])),
        )
        return results

    def _rankings_for_mode(
        self,
        query: str,
        documents: list[IndexedDocument],
        *,
        mode: RetrievalMode,
        filters: MetadataFilter,
    ) -> dict[str, list[RankedCandidate]]:
        if mode == "bm25":
            return {
                "bm25": self._search_bm25(
                    query,
                    documents=documents,
                    filters=filters,
                ),
            }

        if mode == "vector":
            return {
                "semantic": self._rank_semantic(
                    query,
                    documents,
                    filters=filters,
                ),
            }

        if mode == "hybrid":
            return {
                "exact": rank_exact(query, documents),
                "bm25": self._search_bm25(
                    query,
                    documents=documents,
                    filters=filters,
                ),
                "semantic": self._rank_semantic(
                    query,
                    documents,
                    filters=filters,
                ),
            }

        raise ValueError(f"Unsupported retrieval mode: {mode}")

    def refresh(self) -> None:
        self._signature = None
        self._ensure_bm25_index()

    def _ensure_bm25_index(self) -> list[IndexedDocument]:
        raw_documents = list(getattr(self.store, "documents", []) or [])
        signature = tuple(
            (
                doc.get("id"),
                str(doc.get("title", "")),
                str(doc.get("content", "")),
                str(doc.get("source", "")),
                metadata_signature(doc),
            )
            for doc in raw_documents
        )
        if signature == self._signature:
            return self._documents

        self._signature = signature
        self._documents = normalize_documents(raw_documents)
        self._bm25_index = BM25Index(self._documents)
        log_event("retrieval.bm25.rebuilt", documents=len(self._documents))
        return self._documents

    def _search_bm25(
        self,
        query: str,
        *,
        documents: list[IndexedDocument],
        filters: MetadataFilter,
    ) -> list[RankedCandidate]:
        if not documents:
            return []
        if not filters and documents is self._documents:
            return self._bm25_index.search(query, top_k=self.bm25_top_k)
        return BM25Index(documents).search(query, top_k=self.bm25_top_k)

    def _rank_semantic(
        self,
        query: str,
        documents: list[IndexedDocument],
        *,
        filters: MetadataFilter,
    ) -> list[RankedCandidate]:
        return SemanticRanker(self.store).rank(
            query,
            documents,
            top_k=self.vector_top_k,
            filters=filters,
        )


# Private compatibility aliases for the staged package split.
_tokenize = tokenize
_is_cjk = is_cjk
_normalize_documents = normalize_documents
_document_key = document_key
_rank_exact = rank_exact
_document_for_chunk = document_for_chunk
_semantic_score = semantic_score
_reciprocal_rank_fusion = reciprocal_rank_fusion
_best_signal_rank = best_signal_rank
_clean_metadata = clean_signal_metadata
_format_result = format_result
_filter_documents = filter_documents
_metadata_signature = metadata_signature


__all__ = ["HybridRetriever", "MetadataFilter", "RetrievalMode"]
