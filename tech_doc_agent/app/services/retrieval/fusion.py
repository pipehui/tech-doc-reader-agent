from __future__ import annotations

from typing import Any

from tech_doc_agent.app.services.retrieval.models import FusedCandidate, RankedCandidate


def reciprocal_rank_fusion(
    rankings: dict[str, list[RankedCandidate]],
    *,
    rrf_k: int,
) -> list[FusedCandidate]:
    fused: dict[str, FusedCandidate] = {}
    for match_type, ranked_items in rankings.items():
        for rank, candidate in enumerate(ranked_items, start=1):
            item = fused.setdefault(
                candidate.key,
                FusedCandidate(document=candidate.document),
            )
            item.score += 1 / (rrf_k + rank)
            item.match_types.add(match_type)
            item.signals[match_type] = {
                "rank": rank,
                "score": round(candidate.score, 6),
                **clean_signal_metadata(candidate.metadata),
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
            best_signal_rank(item),
            item.document.title,
        ),
    )


def best_signal_rank(candidate: FusedCandidate) -> int:
    return min(
        (signal.get("rank", 9999) for signal in candidate.signals.values()),
        default=9999,
    )


def clean_signal_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = {}
    for key, value in metadata.items():
        if key == "chunk_text" or value is None:
            continue
        cleaned[key] = round(value, 6) if isinstance(value, float) else value
    return cleaned


__all__ = ["reciprocal_rank_fusion"]
