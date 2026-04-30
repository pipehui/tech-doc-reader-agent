from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.core.tenant import TenantContext, tenant_from_values


DEFAULT_PROFILE = {
    "experience_level": "初学者",
    "explanation_style": "先讲原理再看代码",
    "depth": "详细，多举例",
    "language": "中文为主，技术术语保留英文",
}


def get_user_profile_summary(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    settings: Settings | None = None,
) -> str:
    tenant = tenant_from_values(user_id, namespace)
    profile = {
        **DEFAULT_PROFILE,
        **_load_user_profile(tenant, settings=settings),
    }

    return (
        f"用户ID：{tenant.user_id}\n"
        f"知识库命名空间：{tenant.namespace}\n"
        "用户学习偏好：\n"
        f"- 经验水平：{profile['experience_level']}\n"
        f"- 解释风格：{profile['explanation_style']}\n"
        f"- 解释深度：{profile['depth']}\n"
        f"- 语言偏好：{profile['language']}"
    )


def get_user_context_summary(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    memory_query: str = "",
    memory_limit: int = 5,
    settings: Settings | None = None,
) -> str:
    tenant = tenant_from_values(user_id, namespace)
    summary = get_user_profile_summary(
        user_id=tenant.user_id,
        namespace=tenant.namespace,
        settings=settings,
    )
    memories = _load_user_memories(
        tenant,
        query=memory_query,
        limit=memory_limit,
    )
    if not memories:
        return summary

    memory_lines = [
        f"- [{memory['kind']}] {memory['topic']}：{memory['content']}"
        for memory in memories
        if memory.get("content")
    ]
    if not memory_lines:
        return summary

    return summary + "\n长期学习轨迹记忆：\n" + "\n".join(memory_lines)


def _load_user_profile(
    tenant: TenantContext,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    path = Path(settings.DATA_PATH) / "user_profiles" / f"{tenant.user_id}.json"
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    return loaded if isinstance(loaded, dict) else {}


def _load_user_memories(
    tenant: TenantContext,
    *,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        from tech_doc_agent.app.services.resources import get_app_resources

        memory_store = getattr(get_app_resources(), "memory_store", None)
        if memory_store is None:
            return []
        if query:
            return memory_store.read_by_query(
                query,
                user_id=tenant.user_id,
                namespace=tenant.namespace,
                limit=limit,
            )
        return memory_store.read_recent(
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            limit=limit,
        )
    except Exception:
        return []
