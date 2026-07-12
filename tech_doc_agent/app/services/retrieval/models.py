from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


RetrievalMode = Literal["bm25", "vector", "hybrid"]
MetadataFilter = dict[str, Any]
MatchType = Literal["exact", "bm25", "semantic"]


@dataclass(frozen=True, slots=True)
class SearchQuery:
    query: str
    top_k: int | None = None
    mode: RetrievalMode = "hybrid"
    filters: MetadataFilter = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in ("bm25", "vector", "hybrid"):
            raise ValueError(f"Unsupported retrieval mode: {self.mode}")
        object.__setattr__(self, "filters", dict(self.filters))


@dataclass(frozen=True, slots=True)
class SearchResult:
    doc_id: Any
    title: str
    content: str
    source: str
    metadata: dict[str, Any]
    match_types: tuple[MatchType, ...]
    score: float
    signals: dict[str, dict[str, Any]]
    matched_chunks: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(
            self,
            "signals",
            {name: dict(signal) for name, signal in self.signals.items()},
        )
        object.__setattr__(
            self,
            "matched_chunks",
            tuple(dict(chunk) for chunk in self.matched_chunks),
        )

    @property
    def match_type(self) -> str:
        return "+".join(self.match_types)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.doc_id,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "metadata": dict(self.metadata),
            "match_type": self.match_type,
            "score": self.score,
            "retrieval": {
                "score_type": "rrf",
                "signals": {name: dict(signal) for name, signal in self.signals.items()},
            },
        }
        if self.matched_chunks:
            result["matched_chunks"] = [dict(chunk) for chunk in self.matched_chunks]
        return result


class SemanticSearchPort(Protocol):
    def search_related(self, query: str, k: int) -> list[dict[str, Any]]: ...


class RetrievalStorePort(SemanticSearchPort, Protocol):
    documents: list[dict[str, Any]]


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


__all__ = [
    "FusedCandidate",
    "IndexedDocument",
    "MatchType",
    "MetadataFilter",
    "RankedCandidate",
    "RetrievalStorePort",
    "RetrievalMode",
    "SearchQuery",
    "SearchResult",
    "SemanticSearchPort",
]
