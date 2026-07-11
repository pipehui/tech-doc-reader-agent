from __future__ import annotations

from typing import Any

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.services.retrieval.documents import document_key
from tech_doc_agent.app.services.retrieval.filters import metadata_matches
from tech_doc_agent.app.services.retrieval.models import (
    IndexedDocument,
    MetadataFilter,
    RankedCandidate,
    SemanticSearchPort,
)
from tech_doc_agent.app.services.retrieval.normalization import normalize_metadata


class SemanticRanker:
    def __init__(self, store: SemanticSearchPort) -> None:
        self.store = store

    def rank(
        self,
        query: str,
        documents: list[IndexedDocument],
        *,
        top_k: int,
        filters: MetadataFilter,
    ) -> list[RankedCandidate]:
        if top_k <= 0:
            return []

        try:
            candidate_k = top_k * 5 if filters else top_k
            chunks = self.store.search_related(query, k=candidate_k)
        except Exception as exc:
            log_event(
                "retrieval.semantic.skipped",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return []

        by_key = {document.key: document for document in documents}
        by_doc_id = {str(document.doc_id): document for document in documents}
        ranked: list[RankedCandidate] = []
        seen: set[str] = set()
        for rank, chunk in enumerate(chunks, start=1):
            document = document_for_chunk(
                chunk,
                by_key=by_key,
                by_doc_id=by_doc_id,
                allow_fallback=not filters,
            )
            if document is None or document.key in seen:
                continue
            if filters and not metadata_matches(document.raw, filters):
                continue
            seen.add(document.key)
            ranked.append(
                RankedCandidate(
                    key=document.key,
                    document=document,
                    score=semantic_score(chunk, rank),
                    metadata={
                        "distance": chunk.get("distance"),
                        "chunk_index": chunk.get("chunk_index"),
                        "chunk_text": str(chunk.get("chunk_text", "")),
                    },
                )
            )
        return ranked


def document_for_chunk(
    chunk: dict[str, Any],
    *,
    by_key: dict[str, IndexedDocument],
    by_doc_id: dict[str, IndexedDocument],
    allow_fallback: bool = True,
) -> IndexedDocument | None:
    doc_id = chunk.get("doc_id")
    if doc_id is not None:
        document = by_doc_id.get(str(doc_id))
        if document is not None:
            return document

    title = str(chunk.get("title", ""))
    for document in by_key.values():
        if document.title == title:
            return document

    if not allow_fallback:
        return None

    content = str(chunk.get("chunk_text", ""))
    source = str(chunk.get("source", ""))
    metadata = normalize_metadata(chunk)
    key = document_key(
        doc_id=doc_id,
        title=title,
        fallback_index=len(by_key),
    )
    return IndexedDocument(
        key=key,
        doc_id=doc_id,
        title=title,
        content=content,
        source=source,
        metadata=metadata,
        raw={
            "id": doc_id,
            "title": title,
            "content": content,
            "source": source,
            "metadata": metadata,
        },
    )


def semantic_score(chunk: dict[str, Any], rank: int) -> float:
    distance = chunk.get("distance")
    if isinstance(distance, int | float):
        return 1 / (1 + max(float(distance), 0.0))
    return 1 / rank


__all__ = ["SemanticRanker", "document_for_chunk", "semantic_score"]
