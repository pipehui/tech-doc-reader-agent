from __future__ import annotations

from tech_doc_agent.app.application.retrieval import MatchType, SearchResult
from .models import (
    FusedCandidate,
)


MATCH_TYPE_ORDER: tuple[MatchType, ...] = ("exact", "bm25", "semantic")


def format_result(candidate: FusedCandidate) -> SearchResult:
    document = candidate.document
    match_types: tuple[MatchType, ...] = tuple(
        match_type
        for match_type in MATCH_TYPE_ORDER
        if match_type in candidate.match_types
    )
    return SearchResult(
        doc_id=document.doc_id,
        title=document.title,
        content=document.content,
        source=document.source,
        metadata=document.metadata,
        match_types=match_types,
        score=round(candidate.score, 6),
        signals=candidate.signals,
        matched_chunks=tuple(candidate.matched_chunks[:2]),
    )


__all__ = ["format_result"]
