from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tech_doc_agent.app.core.tenant import DEFAULT_NAMESPACE, DEFAULT_USER_ID
from .inference import infer_category, infer_tags
from .taxonomy import tagify


METADATA_KEYS = ("user_id", "namespace", "category", "tags")


def normalize_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(doc)
    normalized["metadata"] = normalize_metadata(normalized)
    return normalized


def normalize_metadata(
    item: Mapping[str, Any] | None,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item = item or {}
    fallback = fallback or {}
    existing = item.get("metadata")
    metadata = dict(existing) if isinstance(existing, Mapping) else {}

    merged = {**fallback, **item, **metadata}
    title = str(item.get("title") or fallback.get("title") or "")
    content = str(item.get("content") or fallback.get("content") or "")

    user_id = clean_scalar(merged.get("user_id")) or DEFAULT_USER_ID
    namespace = clean_scalar(merged.get("namespace")) or DEFAULT_NAMESPACE
    category = clean_scalar(merged.get("category")) or infer_category(
        title=title,
        content=content,
    )
    tags = normalize_tags(merged.get("tags"))
    if not tags:
        tags = infer_tags(title=title, category=category)

    return {
        "user_id": user_id,
        "namespace": namespace,
        "category": category,
        "tags": tags,
    }


def normalize_chunk_metadata(
    chunk: Mapping[str, Any],
    document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(chunk)
    document_metadata = normalize_metadata(document or {}) if document else {}
    chunk_metadata = normalize_metadata(normalized, fallback=document_metadata)
    normalized["metadata"] = chunk_metadata
    for key in METADATA_KEYS:
        normalized[key] = chunk_metadata[key]
    return normalized


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_tags = [value]
    elif isinstance(value, Mapping):
        raw_tags = [str(key) for key, enabled in value.items() if enabled]
    elif isinstance(value, list | tuple | set):
        raw_tags = [str(item) for item in value]
    else:
        raw_tags = [str(value)]

    tags = {tagify(tag) for tag in raw_tags if str(tag).strip()}
    return sorted(tag for tag in tags if tag)


def clean_scalar(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


__all__ = [
    "METADATA_KEYS",
    "clean_scalar",
    "normalize_chunk_metadata",
    "normalize_document",
    "normalize_metadata",
    "normalize_tags",
]
