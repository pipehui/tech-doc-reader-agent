from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_USER_ID = "default"
DEFAULT_NAMESPACE = "tech_docs"
UNCATEGORIZED = "uncategorized"

METADATA_KEYS = ("user_id", "namespace", "category", "tags")

_CATEGORY_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("langgraph_advanced", ("langgraph 进阶", "langgraph checksaver", "langgraph memorysaver", "langgraph redissaver", "langgraph postgressaver")),
    ("langgraph_core", ("langgraph 核心", "langgraph stategraph")),
    ("agent_arch", ("agent 架构", "router agent", "planner agent", "worker agent", "supervisor pattern", "handoff pattern", "react agent", "plan-and-execute", "tool-calling agent", "multi-agent collaboration")),
    ("tool_calling", ("tool calling", "openai function calling")),
    ("rag_advanced", ("rag 进阶",)),
    ("rag_basic", ("rag 基础", "rag（", "rag ", "rag chunk", "rag metadata")),
    ("vector_db", ("向量数据库", "faiss", "qdrant", "milvus", "pgvector", "chroma")),
    ("langchain", ("langchain", "langsmith")),
    ("fastapi", ("fastapi",)),
    ("backend", ("后端工程", "pydantic-settings")),
    ("observability", ("可观测性", "opentelemetry", "langfuse tracing")),
    ("eval", ("评测体系",)),
    ("data_cache", ("数据与缓存", "redis persistence", "redis ", "alembic migration")),
    ("api_design", ("api 与系统设计", "http status code", "jwt", "oauth", "rbac")),
    ("llm_engineering", ("llm 应用工程", "prompt injection")),
)

_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "langgraph_advanced",
        (
            "langgraph 进阶",
            "memorysaver",
            "redissaver",
            "postgressaver",
            "checkpoint namespace",
            "checkpoint_ns",
            "time travel",
            "human-in-the-loop",
            "interrupt_before",
            "interrupt_after",
            "parallel branches",
            "fan-out",
            "fan-in",
            "recursion limit",
            "langgraph studio",
            "multi agent handoff",
            "多 agent handoff",
        ),
    ),
    (
        "langgraph_core",
        (
            "langgraph 核心",
            "langgraph stategraph",
            "graph 概念",
            "stategraph",
            "typeddict state",
            "pydantic state",
            "reducer",
            "add_messages",
            "pregel",
            "subgraph",
            "conditional edge",
            "checkpoint 机制",
            "interrupt 机制",
        ),
    ),
    (
        "agent_arch",
        (
            "agent 架构",
            "router agent",
            "planner agent",
            "worker agent",
            "supervisor pattern",
            "handoff pattern",
            "tool-calling agent",
            "react agent",
            "plan-and-execute",
            "reflection",
            "self-critique",
            "multi-agent collaboration",
            "agent state design",
            "agent memory",
            "agent retry",
            "fallback 机制",
        ),
    ),
    (
        "tool_calling",
        (
            "tool calling",
            "function calling",
            "tool schema",
            "tool_choice",
            "parallel tool calls",
            "tool call id",
            "tool result message",
            "structured output",
            "json mode",
            "pydantic tool schema",
            "sensitive tools",
            "tool approval",
            "tool timeout",
            "tool error handling",
            "tool observability",
        ),
    ),
    (
        "rag_advanced",
        (
            "rag 进阶",
            "recall@k",
            "mrr",
            "ndcg",
            "negative retrieval",
            "semantic drift",
            "lost in the middle",
            "multi-query",
            "hyde",
            "parent-child",
            "late chunking",
            "semantic chunking",
            "context window management",
            "retrieval evaluation dataset",
        ),
    ),
    (
        "rag_basic",
        (
            "rag 基础",
            "chunking",
            "chunk size",
            "chunk overlap",
            "embedding",
            "vector search",
            "bm25",
            "hybrid search",
            "rrf",
            "reranker",
            "query rewrite",
            "metadata filter",
            "document loader",
            "retrieval pipeline",
            "context compression",
            "citation",
            "grounding",
            "检索增强生成",
        ),
    ),
    (
        "vector_db",
        (
            "向量数据库",
            "faiss",
            "qdrant",
            "milvus",
            "pgvector",
            "chroma",
            "vector distance",
            "cosine similarity",
            "l2 distance",
            "inner product",
            "ann index",
            "hnsw",
            "ivf",
            "product quantization",
        ),
    ),
    (
        "langchain",
        (
            "langchain",
            "chatmodel",
            "runnable",
            "prompttemplate",
            "chatprompttemplate",
            "messagesplaceholder",
            "outputparser",
            "lcel",
            "langsmith",
        ),
    ),
    (
        "fastapi",
        (
            "fastapi",
            "apirouter",
            "depends",
            "dependency injection",
            "yield dependency",
            "lifespan",
            "middleware",
            "exception handler",
            "streamingresponse",
            "sse",
            "cors",
            "backgroundtasks",
            "testclient",
            "async route",
        ),
    ),
    (
        "backend",
        (
            "后端工程",
            "settings management",
            "pydantic-settings",
            "environment variables",
            "docker compose",
            "health check",
            "readiness probe",
            "structured logging",
            "trace_id",
            "request_id",
            "graceful shutdown",
            "resource container",
            "lazy initialization",
        ),
    ),
    (
        "observability",
        (
            "可观测性",
            "langfuse",
            "opentelemetry",
            "structured logs",
            "span",
            "trace",
            "callback handler",
            "token usage",
            "latency p50",
            "latency p95",
            "error rate",
            "tool timing",
            "node timing",
            "sse event timing",
            "eval report",
            "dashboard",
        ),
    ),
    (
        "eval",
        (
            "评测体系",
            "agent eval",
            "single-turn eval",
            "multi-turn eval",
            "plan match",
            "keyword score",
            "latency score",
            "tool call count",
            "interrupted status",
            "baseline",
            "regression test",
            "golden dataset",
            "judge function",
            "offline eval",
            "online eval",
        ),
    ),
    (
        "data_cache",
        (
            "数据与缓存",
            "redis",
            "rdb",
            "aof",
            "redis stream",
            "redis pub/sub",
            "cache aside",
            "ttl",
            "lru",
            "session store",
            "sqlite",
            "postgresql",
            "sqlmodel",
            "sqlalchemy",
            "alembic",
        ),
    ),
    (
        "api_design",
        (
            "api 与系统设计",
            "restful api",
            "http status code",
            "idempotency",
            "pagination",
            "rate limiting",
            "authentication",
            "jwt",
            "oauth",
            "rbac",
            "openapi",
            "webhook",
            "retry policy",
            "circuit breaker",
        ),
    ),
    (
        "llm_engineering",
        (
            "llm 应用工程",
            "prompt injection",
            "jailbreak",
            "pii redaction",
            "guardrails",
            "model routing",
            "fallback model",
            "cost control",
            "token budget",
            "streaming output",
            "empty response retry",
            "llm timeout",
            "llm rate limit",
            "response validation",
        ),
    ),
)

_VALID_CATEGORIES = {category for category, _ in _CATEGORY_RULES}

_CATEGORY_ALIASES = {
    "langgraph核心": "langgraph_core",
    "langgraph_core": "langgraph_core",
    "langgraph进阶": "langgraph_advanced",
    "langgraph_advanced": "langgraph_advanced",
    "agent架构": "agent_arch",
    "agent_arch": "agent_arch",
    "toolcalling": "tool_calling",
    "tool_calling": "tool_calling",
    "functioncalling": "tool_calling",
    "rag基础": "rag_basic",
    "rag_basic": "rag_basic",
    "ragbasic": "rag_basic",
    "rag进阶": "rag_advanced",
    "rag_advanced": "rag_advanced",
    "ragadvanced": "rag_advanced",
    "向量数据库": "vector_db",
    "vectordb": "vector_db",
    "vector_db": "vector_db",
    "后端工程": "backend",
    "可观测性": "observability",
    "评测体系": "eval",
    "数据与缓存": "data_cache",
    "data_cache": "data_cache",
    "api与系统设计": "api_design",
    "api_design": "api_design",
    "llm应用工程": "llm_engineering",
    "llm_engineering": "llm_engineering",
}

_BROAD_CATEGORY_TAGS = {
    "rag": ["rag"],
    "rag相关": ["rag"],
    "rag有关": ["rag"],
    "langgraph": ["langgraph"],
    "langgraph相关": ["langgraph"],
    "langgraph有关": ["langgraph"],
}


def normalize_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(doc)
    normalized["metadata"] = normalize_metadata(normalized)
    return normalized


def normalize_metadata(item: Mapping[str, Any] | None, *, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    item = item or {}
    fallback = fallback or {}
    existing = item.get("metadata")
    metadata = dict(existing) if isinstance(existing, Mapping) else {}

    merged = {**fallback, **item, **metadata}
    title = str(item.get("title") or fallback.get("title") or "")
    content = str(item.get("content") or fallback.get("content") or "")

    user_id = _clean_scalar(merged.get("user_id")) or DEFAULT_USER_ID
    namespace = _clean_scalar(merged.get("namespace")) or DEFAULT_NAMESPACE
    category = _clean_scalar(merged.get("category")) or infer_category(title=title, content=content)
    tags = normalize_tags(merged.get("tags"))
    if not tags:
        tags = infer_tags(title=title, category=category)

    return {
        "user_id": user_id,
        "namespace": namespace,
        "category": category,
        "tags": tags,
    }


def normalize_chunk_metadata(chunk: Mapping[str, Any], document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    normalized = dict(chunk)
    document_metadata = normalize_metadata(document or {}) if document else {}
    chunk_metadata = normalize_metadata(normalized, fallback=document_metadata)
    normalized["metadata"] = chunk_metadata
    for key in METADATA_KEYS:
        normalized[key] = chunk_metadata[key]
    return normalized


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
                normalized["tags"] = sorted(set(normalized.get("tags", [])) | set(tags))
            continue
        if key == "category":
            category, tags = normalize_category_filter(value)
            if category:
                normalized["category"] = category
            if tags:
                normalized["tags"] = sorted(set(normalized.get("tags", [])) | set(tags))
            continue
        normalized[key] = value
    return normalized


def normalize_category_filter(value: Any) -> tuple[str, list[str]]:
    category = _clean_scalar(value)
    if not category:
        return "", []

    category_key = _category_alias_key(category)
    if category_key in _BROAD_CATEGORY_TAGS:
        return "", _BROAD_CATEGORY_TAGS[category_key]

    if category_key in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[category_key], []

    if category in _VALID_CATEGORIES:
        return category, []

    inferred = infer_category(title=category, content=category)
    if inferred != UNCATEGORIZED:
        return inferred, []

    return category, []


def metadata_matches(item: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
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


def infer_category(*, title: str, content: str = "") -> str:
    title_haystack = title.casefold()
    for category, prefixes in _CATEGORY_PREFIXES:
        if any(title_haystack.startswith(prefix.casefold()) for prefix in prefixes):
            return category

    for category, keywords in _CATEGORY_RULES:
        if any(keyword.casefold() in title_haystack for keyword in keywords):
            return category

    content_haystack = content[:800].casefold()
    for category, keywords in _CATEGORY_RULES:
        if any(keyword.casefold() in content_haystack for keyword in keywords):
            return category
    return UNCATEGORIZED


def infer_tags(*, title: str, category: str) -> list[str]:
    title_lower = title.casefold()
    tags = {category}

    for _, keywords in _CATEGORY_RULES:
        for keyword in keywords:
            if keyword.casefold() in title_lower:
                tags.add(_tagify(keyword))

    for token in ("langgraph", "langchain", "fastapi", "rag", "redis", "faiss", "qdrant", "openai"):
        if token in title_lower:
            tags.add(token)

    return sorted(tag for tag in tags if tag)


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

    tags = {_tagify(tag) for tag in raw_tags if str(tag).strip()}
    return sorted(tag for tag in tags if tag)


def _clean_scalar(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _tagify(value: str) -> str:
    return value.strip().casefold().replace(" ", "_").replace("（", "_").replace("）", "").replace("(", "_").replace(")", "")


def _category_alias_key(value: str) -> str:
    return (
        value.casefold()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
        .replace("相关的内容", "相关")
        .replace("相关内容", "相关")
        .replace("有关的内容", "有关")
        .replace("有关内容", "有关")
    )


def _value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list | tuple | set):
        return any(_value_matches(actual, item) for item in expected)
    if isinstance(actual, list | tuple | set):
        expected_text = str(expected).strip().casefold()
        return any(str(item).strip().casefold() == expected_text for item in actual)
    return str(actual or "").strip().casefold() == str(expected or "").strip().casefold()
