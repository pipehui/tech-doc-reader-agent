from __future__ import annotations

from typing import Any

from tech_doc_agent.app.services.retrieval.models import FusedCandidate


MATCH_TYPE_ORDER = ("exact", "bm25", "semantic")


def format_result(candidate: FusedCandidate) -> dict[str, Any]:
    document = candidate.document
    match_types = [
        match_type
        for match_type in MATCH_TYPE_ORDER
        if match_type in candidate.match_types
    ]
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


__all__ = ["format_result"]
