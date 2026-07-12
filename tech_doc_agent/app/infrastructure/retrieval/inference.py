from __future__ import annotations

from .taxonomy import (
    CATEGORY_PREFIXES,
    CATEGORY_RULES,
    UNCATEGORIZED,
    tagify,
)


def infer_category(*, title: str, content: str = "") -> str:
    title_haystack = title.casefold()
    for category, prefixes in CATEGORY_PREFIXES:
        if any(title_haystack.startswith(prefix.casefold()) for prefix in prefixes):
            return category

    for category, keywords in CATEGORY_RULES:
        if any(keyword.casefold() in title_haystack for keyword in keywords):
            return category

    content_haystack = content[:800].casefold()
    for category, keywords in CATEGORY_RULES:
        if any(keyword.casefold() in content_haystack for keyword in keywords):
            return category
    return UNCATEGORIZED


def infer_tags(*, title: str, category: str) -> list[str]:
    title_lower = title.casefold()
    tags = {category}

    for _, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if keyword.casefold() in title_lower:
                tags.add(tagify(keyword))

    for token in (
        "langgraph",
        "langchain",
        "fastapi",
        "rag",
        "redis",
        "faiss",
        "qdrant",
        "openai",
    ):
        if token in title_lower:
            tags.add(token)

    return sorted(tag for tag in tags if tag)


__all__ = ["infer_category", "infer_tags"]
