from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


RetrievalMode = Literal["bm25", "vector", "hybrid"]
MetadataFilter = dict[str, Any]


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
    "MetadataFilter",
    "RankedCandidate",
    "RetrievalStorePort",
    "RetrievalMode",
    "SemanticSearchPort",
]
