from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from tech_doc_agent.app.application.retrieval import (
    MetadataFilter,
    RetrievalMode,
    SearchQuery,
    SearchResult,
)
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
    RankedCandidate,
    RetrievalStorePort,
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


_IndexSignature = tuple[tuple[Any, str, str, str, str], ...]


@dataclass(frozen=True)
class _IndexSnapshot:
    signature: _IndexSignature
    documents: tuple[IndexedDocument, ...]
    bm25_index: BM25Index


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
        self.top_k = self.settings.HYBRID_RAG_TOP_K if top_k is None else top_k
        self.bm25_top_k = self.settings.HYBRID_RAG_BM25_TOP_K if bm25_top_k is None else bm25_top_k
        self.vector_top_k = self.settings.HYBRID_RAG_VECTOR_TOP_K if vector_top_k is None else vector_top_k
        self.rrf_k = self.settings.HYBRID_RAG_RRF_K if rrf_k is None else rrf_k
        self._index_lock = threading.Lock()
        self._index_snapshot: _IndexSnapshot | None = None

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        mode: RetrievalMode = "hybrid",
        filters: MetadataFilter | None = None,
    ) -> list[dict[str, Any]]:
        results = self.retrieve(
            SearchQuery(
                query=query,
                top_k=top_k,
                mode=mode,
                filters=filters or {},
            )
        )
        return [result.to_dict() for result in results]

    def retrieve(self, request: SearchQuery) -> list[SearchResult]:
        top_k = self.top_k if request.top_k is None else request.top_k
        if top_k <= 0:
            return []
        filters = normalize_filter(request.filters)

        snapshot = self._ensure_index_snapshot()
        if not snapshot.documents:
            log_event(
                "retrieval.hybrid.finished",
                mode=request.mode,
                filters=filters,
                result_count=0,
                exact_count=0,
                bm25_count=0,
                semantic_count=0,
            )
            return []

        filtered_documents: Sequence[IndexedDocument] = snapshot.documents
        if filters:
            filtered_documents = filter_documents(snapshot.documents, filters)
        rankings = self._rankings_for_mode(
            request.query,
            filtered_documents,
            snapshot=snapshot,
            mode=request.mode,
            filters=filters,
        )
        fused = reciprocal_rank_fusion(rankings, rrf_k=self.rrf_k)
        results = [format_result(item) for item in fused[:top_k]]
        log_event(
            "retrieval.hybrid.finished",
            mode=request.mode,
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
        documents: Sequence[IndexedDocument],
        *,
        snapshot: _IndexSnapshot,
        mode: RetrievalMode,
        filters: MetadataFilter,
    ) -> dict[str, list[RankedCandidate]]:
        if mode == "bm25":
            return {
                "bm25": self._search_bm25(
                    query,
                    documents=documents,
                    snapshot=snapshot,
                    filters=filters,
                ),
            }

        if mode == "vector":
            return {
                "semantic": self._rank_semantic(
                    query,
                    documents,
                    filters=filters,
                    degrade_on_failure=False,
                ),
            }

        if mode == "hybrid":
            return {
                "exact": rank_exact(query, documents),
                "bm25": self._search_bm25(
                    query,
                    documents=documents,
                    snapshot=snapshot,
                    filters=filters,
                ),
                "semantic": self._rank_semantic(
                    query,
                    documents,
                    filters=filters,
                    degrade_on_failure=True,
                ),
            }

        raise ValueError(f"Unsupported retrieval mode: {mode}")

    def refresh(self) -> None:
        self._ensure_index_snapshot(force_rebuild=True)

    def _ensure_index_snapshot(self, *, force_rebuild: bool = False) -> _IndexSnapshot:
        with self._index_lock:
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
            current = self._index_snapshot
            if not force_rebuild and current is not None and signature == current.signature:
                return current

            documents = tuple(normalize_documents(raw_documents))
            snapshot = _IndexSnapshot(
                signature=signature,
                documents=documents,
                bm25_index=BM25Index(documents),
            )
            self._index_snapshot = snapshot

        log_event("retrieval.bm25.rebuilt", documents=len(snapshot.documents))
        return snapshot

    def _search_bm25(
        self,
        query: str,
        *,
        documents: Sequence[IndexedDocument],
        snapshot: _IndexSnapshot,
        filters: MetadataFilter,
    ) -> list[RankedCandidate]:
        if not documents:
            return []
        if not filters:
            return snapshot.bm25_index.search(query, top_k=self.bm25_top_k)
        return BM25Index(documents).search(query, top_k=self.bm25_top_k)

    def _rank_semantic(
        self,
        query: str,
        documents: Sequence[IndexedDocument],
        *,
        filters: MetadataFilter,
        degrade_on_failure: bool,
    ) -> list[RankedCandidate]:
        return SemanticRanker(self.store).rank(
            query,
            documents,
            top_k=self.vector_top_k,
            filters=filters,
            degrade_on_failure=degrade_on_failure,
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


__all__ = [
    "HybridRetriever",
    "MetadataFilter",
    "RetrievalMode",
    "SearchQuery",
    "SearchResult",
]
