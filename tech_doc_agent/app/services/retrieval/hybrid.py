from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.services.retrieval.metadata import (
    metadata_matches,
    normalize_filter,
    normalize_metadata,
)


TOKEN_RE = re.compile(r"[A-Za-z0-9_+#.-]+|[\u4e00-\u9fff]+")
CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")
MATCH_TYPE_ORDER = ("exact", "bm25", "semantic")
RetrievalMode = Literal["bm25", "vector", "hybrid"]
MetadataFilter = dict[str, Any]


@dataclass(frozen=True)
class IndexedDocument:
    key: str
    doc_id: Any
    title: str
    content: str
    source: str
    metadata: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class RankedCandidate:
    key: str
    document: IndexedDocument
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FusedCandidate:
    document: IndexedDocument
    score: float = 0.0
    match_types: set[str] = field(default_factory=set)
    signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    matched_chunks: list[dict[str, Any]] = field(default_factory=list)


class BM25Index:
    def __init__(self, documents: list[IndexedDocument], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.doc_tokens = [_tokenize(f"{doc.title}\n{doc.content}") for doc in documents]
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.avg_doc_length = sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0.0
        self.term_frequencies = [Counter(tokens) for tokens in self.doc_tokens]
        self.idf = self._build_idf()

    def _build_idf(self) -> dict[str, float]:
        doc_count = len(self.doc_tokens)
        document_frequency: Counter[str] = Counter()
        for tokens in self.doc_tokens:
            document_frequency.update(set(tokens))

        return {
            term: math.log(1 + (doc_count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(self, query: str, *, top_k: int) -> list[RankedCandidate]:
        if top_k <= 0 or not self.documents:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        query_terms = Counter(query_tokens)
        scored: list[RankedCandidate] = []
        for index, document in enumerate(self.documents):
            score = self._score_document(index, query_terms)
            if score <= 0:
                continue
            scored.append(
                RankedCandidate(
                    key=document.key,
                    document=document,
                    score=score,
                    metadata={"bm25_score": score},
                )
            )

        return sorted(scored, key=lambda item: (-item.score, item.document.title))[:top_k]

    def _score_document(self, document_index: int, query_terms: Counter[str]) -> float:
        term_frequency = self.term_frequencies[document_index]
        doc_length = self.doc_lengths[document_index]
        score = 0.0

        for term, query_weight in query_terms.items():
            term_count = term_frequency.get(term, 0)
            if term_count == 0:
                continue

            denominator = term_count + self.k1 * (
                1 - self.b + self.b * doc_length / max(self.avg_doc_length, 1.0)
            )
            score += query_weight * self.idf.get(term, 0.0) * (term_count * (self.k1 + 1)) / denominator

        return score


class HybridRetriever:
    def __init__(
        self,
        store: Any,
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

        filtered_documents = _filter_documents(documents, filters)
        rankings = self._rankings_for_mode(query, filtered_documents, mode=mode, filters=filters)
        fused = _reciprocal_rank_fusion(rankings, rrf_k=self.rrf_k)
        results = [_format_result(item) for item in fused[:top_k]]
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
                "bm25": self._search_bm25(query, documents=documents, filters=filters),
            }

        if mode == "vector":
            return {
                "semantic": self._rank_semantic(query, documents, filters=filters),
            }

        if mode == "hybrid":
            return {
                "exact": _rank_exact(query, documents),
                "bm25": self._search_bm25(query, documents=documents, filters=filters),
                "semantic": self._rank_semantic(query, documents, filters=filters),
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
                _metadata_signature(doc),
            )
            for doc in raw_documents
        )
        if signature == self._signature:
            return self._documents

        self._signature = signature
        self._documents = _normalize_documents(raw_documents)
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
        if self.vector_top_k <= 0:
            return []

        try:
            candidate_k = self.vector_top_k * 5 if filters else self.vector_top_k
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
            document = _document_for_chunk(
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
                    score=_semantic_score(chunk, rank),
                    metadata={
                        "distance": chunk.get("distance"),
                        "chunk_index": chunk.get("chunk_index"),
                        "chunk_text": str(chunk.get("chunk_text", "")),
                    },
                )
            )
        return ranked


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        if _is_cjk(token):
            tokens.extend(token)
            tokens.extend(token[index : index + 2] for index in range(max(len(token) - 1, 0)))
            continue

        lowered = token.lower()
        tokens.append(lowered)
        tokens.extend(part.lower() for part in CAMEL_RE.findall(token) if len(part) > 1)

    return [token for token in tokens if token]


def _is_cjk(token: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in token)


def _normalize_documents(raw_documents: list[dict[str, Any]]) -> list[IndexedDocument]:
    normalized: list[IndexedDocument] = []
    for index, doc in enumerate(raw_documents):
        title = str(doc.get("title", ""))
        content = str(doc.get("content", ""))
        source = str(doc.get("source", ""))
        doc_id = doc.get("id", index + 1)
        metadata = normalize_metadata(doc)
        raw = dict(doc)
        raw["metadata"] = metadata
        key = _document_key(doc_id=doc_id, title=title, fallback_index=index)
        normalized.append(
            IndexedDocument(
                key=key,
                doc_id=doc_id,
                title=title,
                content=content,
                source=source,
                metadata=metadata,
                raw=raw,
            )
        )
    return normalized


def _document_key(*, doc_id: Any, title: str, fallback_index: int) -> str:
    if doc_id is not None:
        return f"id:{doc_id}"
    if title:
        return f"title:{title}"
    return f"index:{fallback_index}"


def _rank_exact(query: str, documents: list[IndexedDocument]) -> list[RankedCandidate]:
    query_lower = query.strip().lower()
    if not query_lower:
        return []

    ranked: list[RankedCandidate] = []
    for document in documents:
        title_lower = document.title.lower()
        content_lower = document.content.lower()
        title_match = query_lower in title_lower
        content_match = query_lower in content_lower
        if not title_match and not content_match:
            continue
        score = 2.0 if title_match else 1.0
        if content_match:
            score += 1.0
        ranked.append(
            RankedCandidate(
                key=document.key,
                document=document,
                score=score,
                metadata={"exact_score": score},
            )
        )

    return sorted(ranked, key=lambda item: (-item.score, item.document.title))


def _document_for_chunk(
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
    key = _document_key(doc_id=doc_id, title=title, fallback_index=len(by_key))
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


def _semantic_score(chunk: dict[str, Any], rank: int) -> float:
    distance = chunk.get("distance")
    if isinstance(distance, int | float):
        return 1 / (1 + max(float(distance), 0.0))
    return 1 / rank


def _reciprocal_rank_fusion(
    rankings: dict[str, list[RankedCandidate]],
    *,
    rrf_k: int,
) -> list[FusedCandidate]:
    fused: dict[str, FusedCandidate] = {}
    for match_type, ranked_items in rankings.items():
        for rank, candidate in enumerate(ranked_items, start=1):
            item = fused.setdefault(candidate.key, FusedCandidate(document=candidate.document))
            item.score += 1 / (rrf_k + rank)
            item.match_types.add(match_type)
            item.signals[match_type] = {
                "rank": rank,
                "score": round(candidate.score, 6),
                **_clean_metadata(candidate.metadata),
            }
            chunk_text = candidate.metadata.get("chunk_text")
            if match_type == "semantic" and chunk_text:
                item.matched_chunks.append(
                    {
                        "text": chunk_text,
                        "chunk_index": candidate.metadata.get("chunk_index"),
                        "distance": candidate.metadata.get("distance"),
                    }
                )

    return sorted(
        fused.values(),
        key=lambda item: (
            -item.score,
            _best_signal_rank(item),
            item.document.title,
        ),
    )


def _best_signal_rank(candidate: FusedCandidate) -> int:
    return min((signal.get("rank", 9999) for signal in candidate.signals.values()), default=9999)


def _clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = {}
    for key, value in metadata.items():
        if key == "chunk_text" or value is None:
            continue
        cleaned[key] = round(value, 6) if isinstance(value, float) else value
    return cleaned


def _format_result(candidate: FusedCandidate) -> dict[str, Any]:
    document = candidate.document
    match_types = [match_type for match_type in MATCH_TYPE_ORDER if match_type in candidate.match_types]
    result = {
        "id": document.doc_id,
        "title": document.title,
        "content": document.content,
        "source": document.source,
        "metadata": document.metadata,
        "match_type": "+".join(match_types),
        "score": round(candidate.score, 6),
        "retrieval": {
            "score_type": "rrf",
            "signals": candidate.signals,
        },
    }
    if candidate.matched_chunks:
        result["matched_chunks"] = candidate.matched_chunks[:2]
    return result


def _filter_documents(documents: list[IndexedDocument], filters: MetadataFilter) -> list[IndexedDocument]:
    if not filters:
        return documents
    return [document for document in documents if metadata_matches(document.raw, filters)]


def _metadata_signature(doc: dict[str, Any]) -> str:
    metadata = normalize_metadata(doc)
    tags = ",".join(metadata.get("tags", []))
    return "|".join(
        [
            str(metadata.get("user_id", "")),
            str(metadata.get("namespace", "")),
            str(metadata.get("category", "")),
            tags,
        ]
    )
