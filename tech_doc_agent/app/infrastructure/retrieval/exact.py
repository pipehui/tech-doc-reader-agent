from collections.abc import Sequence

from .models import IndexedDocument, RankedCandidate


def rank_exact(
    query: str,
    documents: Sequence[IndexedDocument],
) -> list[RankedCandidate]:
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


__all__ = ["rank_exact"]
