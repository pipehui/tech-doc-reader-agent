from __future__ import annotations

import re
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9_+#.-]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_STOP_TOKENS = {
    "about",
    "current",
    "history",
    "learn",
    "learning",
    "memory",
    "previous",
    "record",
    "records",
    "recent",
    "review",
    "summary",
    "user",
    "what",
    "which",
    "哪些",
    "之前",
    "关于",
    "内容",
    "复习",
    "情况",
    "学习",
    "学过",
    "用户",
    "记录",
    "轨迹",
}


def query_matches(query: str, *texts: Any) -> bool:
    query_text = str(query or "").strip().casefold()
    if not query_text:
        return True

    haystack = "\n".join(str(text or "") for text in texts).casefold()
    if query_text in haystack:
        return True

    for text in texts:
        text_value = str(text or "").strip().casefold()
        if text_value and text_value in query_text:
            return True

    haystack_tokens = set(extract_match_tokens(haystack))
    if not haystack_tokens:
        return False

    return any(token in haystack_tokens or token in haystack for token in extract_match_tokens(query_text))


def extract_match_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.findall(str(text or "").casefold()):
        if not match or match in _STOP_TOKENS:
            continue
        if _is_cjk_text(match):
            tokens.extend(_cjk_tokens(match))
        elif len(match) > 1:
            tokens.append(match)
    return [token for token in tokens if token and token not in _STOP_TOKENS]


def _is_cjk_text(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _cjk_tokens(text: str) -> list[str]:
    if len(text) == 1:
        return []
    if len(text) <= 6:
        tokens = [text]
    else:
        tokens = []
    tokens.extend(text[index : index + 2] for index in range(len(text) - 1))
    return tokens
