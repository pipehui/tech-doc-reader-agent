from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tech_doc_agent.app.services.retrieval.inference import infer_category
from tech_doc_agent.app.services.retrieval.normalization import (
    clean_scalar,
    normalize_metadata,
    normalize_tags,
)
from tech_doc_agent.app.services.retrieval.taxonomy import (
    BROAD_CATEGORY_TAGS,
    CATEGORY_ALIASES,
    UNCATEGORIZED,
    VALID_CATEGORIES,
    category_alias_key,
)


def normalize_filter(filters: Mapping[str, Any] | None) -> dict[str, Any]:
    if not filters:
        return {}

    normalized: dict[str, Any] = {}
    for key, value in filters.items():
        if value is None or value == "" or value == []:
            continue
        if key == "metadata" and isinstance(value, Mapping):
            normalized.update(normalize_filter(value))
            continue
        if key == "tags":
            tags = normalize_tags(value)
            if tags:
                normalized["tags"] = sorted(
                    set(normalized.get("tags", [])) | set(tags)
                )
            continue
        if key == "category":
            category, tags = normalize_category_filter(value)
            if category:
                normalized["category"] = category
            if tags:
                normalized["tags"] = sorted(
                    set(normalized.get("tags", [])) | set(tags)
                )
            continue
        normalized[key] = value
    return normalized


def normalize_category_filter(value: Any) -> tuple[str, list[str]]:
    category = clean_scalar(value)
    if not category:
        return "", []

    category_key = category_alias_key(category)
    if category_key in BROAD_CATEGORY_TAGS:
        return "", BROAD_CATEGORY_TAGS[category_key]

    if category_key in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[category_key], []

    if category in VALID_CATEGORIES:
        return category, []

    inferred = infer_category(title=category, content=category)
    if inferred != UNCATEGORIZED:
        return inferred, []

    return category, []


def metadata_matches(
    item: Mapping[str, Any],
    filters: Mapping[str, Any] | None,
) -> bool:
    normalized_filters = normalize_filter(filters)
    if not normalized_filters:
        return True

    metadata = normalize_metadata(item)
    for key, expected in normalized_filters.items():
        if key == "tags":
            actual_tags = set(normalize_tags(metadata.get("tags")))
            expected_tags = set(normalize_tags(expected))
            if not expected_tags.issubset(actual_tags):
                return False
            continue

        if key == "source":
            actual = item.get("source")
        else:
            actual = metadata.get(key, item.get(key))

        if not _value_matches(actual, expected):
            return False

    return True


def _value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list | tuple | set):
        return any(_value_matches(actual, item) for item in expected)
    if isinstance(actual, list | tuple | set):
        expected_text = str(expected).strip().casefold()
        return any(str(item).strip().casefold() == expected_text for item in actual)
    return str(actual or "").strip().casefold() == str(expected or "").strip().casefold()


__all__ = ["metadata_matches", "normalize_category_filter", "normalize_filter"]
