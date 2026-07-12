from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from tech_doc_agent.app.services.retrieval.models import IndexedDocument, RankedCandidate
from tech_doc_agent.app.services.retrieval.tokenization import tokenize


class BM25Index:
    def __init__(
        self,
        documents: Sequence[IndexedDocument],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.documents = tuple(documents)
        self.k1 = k1
        self.b = b
        self.doc_tokens = [tokenize(f"{doc.title}\n{doc.content}") for doc in documents]
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.avg_doc_length = (
            sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0.0
        )
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

        query_tokens = tokenize(query)
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

        return sorted(
            scored,
            key=lambda item: (-item.score, item.document.title),
        )[:top_k]

    def _score_document(
        self,
        document_index: int,
        query_terms: Counter[str],
    ) -> float:
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
            score += (
                query_weight
                * self.idf.get(term, 0.0)
                * (term_count * (self.k1 + 1))
                / denominator
            )

        return score


__all__ = ["BM25Index"]
