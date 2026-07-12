from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tech_doc_agent.app.application.retrieval import MetadataFilter
from tech_doc_agent.app.services.retrieval.filters import metadata_matches
from tech_doc_agent.app.services.retrieval.models import IndexedDocument
from tech_doc_agent.app.services.retrieval.normalization import normalize_metadata


def normalize_documents(
    raw_documents: list[dict[str, Any]],
) -> list[IndexedDocument]:
    normalized: list[IndexedDocument] = []
    for index, doc in enumerate(raw_documents):
        title = str(doc.get("title", ""))
        content = str(doc.get("content", ""))
        source = str(doc.get("source", ""))
        doc_id = doc.get("id", index + 1)
        metadata = normalize_metadata(doc)
        raw = dict(doc)
        raw["metadata"] = metadata
        key = document_key(doc_id=doc_id, title=title, fallback_index=index)
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


def document_key(*, doc_id: Any, title: str, fallback_index: int) -> str:
    if doc_id is not None:
        return f"id:{doc_id}"
    if title:
        return f"title:{title}"
    return f"index:{fallback_index}"


def filter_documents(
    documents: Sequence[IndexedDocument],
    filters: MetadataFilter,
) -> list[IndexedDocument]:
    if not filters:
        return list(documents)
    return [
        document
        for document in documents
        if metadata_matches(document.raw, filters)
    ]


def metadata_signature(doc: dict[str, Any]) -> str:
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


__all__ = [
    "document_key",
    "filter_documents",
    "metadata_signature",
    "normalize_documents",
]
