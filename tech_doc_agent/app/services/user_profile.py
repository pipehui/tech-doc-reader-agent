from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.core.tenant import TenantContext, tenant_from_values


DEFAULT_PROFILE = {
    "experience_level": "初学者",
    "explanation_style": "先讲原理再看代码",
    "depth": "详细，多举例",
    "language": "中文为主，技术术语保留英文",
    "known_topics": [],
    "weak_topics": [],
    "notes": "",
}
PROFILE_VERSION = 1
TEXT_PROFILE_FIELDS = ("experience_level", "explanation_style", "depth", "language", "notes")
LIST_PROFILE_FIELDS = ("known_topics", "weak_topics")


def get_user_profile_summary(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    settings: Settings | None = None,
) -> str:
    tenant = tenant_from_values(user_id, namespace)
    profile = get_user_profile(tenant.user_id, tenant.namespace, settings=settings)

    summary = (
        f"用户ID：{tenant.user_id}\n"
        f"知识库命名空间：{tenant.namespace}\n"
        "用户学习偏好：\n"
        f"- 经验水平：{profile['experience_level']}\n"
        f"- 解释风格：{profile['explanation_style']}\n"
        f"- 解释深度：{profile['depth']}\n"
        f"- 语言偏好：{profile['language']}"
    )
    profile_lines = []
    if profile["known_topics"]:
        profile_lines.append(f"- 已掌握/熟悉主题：{', '.join(profile['known_topics'])}")
    if profile["weak_topics"]:
        profile_lines.append(f"- 仍需巩固主题：{', '.join(profile['weak_topics'])}")
    if profile["notes"]:
        profile_lines.append(f"- 其他画像备注：{profile['notes']}")
    if profile_lines:
        summary += "\n长期用户画像：\n" + "\n".join(profile_lines)
    return summary


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


def get_user_profile(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    tenant = tenant_from_values(user_id, namespace)
    return _normalize_profile(
        {
            **DEFAULT_PROFILE,
            **_load_user_profile(tenant, settings=settings),
        },
        tenant,
    )


def update_user_profile(
    user_id: str | None = None,
    namespace: str | None = None,
    *,
    experience_level: str | None = None,
    explanation_style: str | None = None,
    depth: str | None = None,
    language: str | None = None,
    known_topics: list[str] | None = None,
    weak_topics: list[str] | None = None,
    resolved_weak_topics: list[str] | None = None,
    notes: str | None = None,
    evidence: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    tenant = tenant_from_values(user_id, namespace)
    settings = settings or get_settings()
    profile = get_user_profile(tenant.user_id, tenant.namespace, settings=settings)

    changed = False
    text_updates = {
        "experience_level": experience_level,
        "explanation_style": explanation_style,
        "depth": depth,
        "language": language,
        "notes": notes,
    }
    for field, value in text_updates.items():
        normalized = _string_or_empty(value)
        if normalized and profile[field] != normalized:
            profile[field] = normalized
            changed = True

    merged_known_topics = _merge_unique(profile["known_topics"], known_topics or [])
    if merged_known_topics != profile["known_topics"]:
        profile["known_topics"] = merged_known_topics
        changed = True

    merged_weak_topics = _merge_unique(profile["weak_topics"], weak_topics or [])
    resolved_keys = {_topic_key(topic) for topic in resolved_weak_topics or []}
    known_keys = {_topic_key(topic) for topic in profile["known_topics"]}
    filtered_weak_topics = [
        topic
        for topic in merged_weak_topics
        if _topic_key(topic) not in resolved_keys and _topic_key(topic) not in known_keys
    ]
    if filtered_weak_topics != profile["weak_topics"]:
        profile["weak_topics"] = filtered_weak_topics
        changed = True

    normalized_evidence = _string_or_empty(evidence)
    if normalized_evidence:
        profile["last_update_reason"] = normalized_evidence
        changed = True

    if changed:
        profile["updated_at"] = datetime.now(UTC).isoformat()
        _save_user_profile(tenant, profile, settings=settings)
        profile["status"] = "updated"
    else:
        profile["status"] = "unchanged"

    return profile


def _load_user_profile(
    tenant: TenantContext,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    path = _profile_path(tenant, settings=settings)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    return loaded if isinstance(loaded, dict) else {}


def _save_user_profile(
    tenant: TenantContext,
    profile: dict[str, Any],
    *,
    settings: Settings,
) -> None:
    path = _profile_path(tenant, settings=settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = dict(profile)
    cleaned.pop("namespace", None)
    cleaned.pop("status", None)
    with path.open("w", encoding="utf-8") as file:
        json.dump(cleaned, file, ensure_ascii=False, indent=2)


def _profile_path(tenant: TenantContext, *, settings: Settings) -> Path:
    return Path(settings.DATA_PATH) / "user_profiles" / f"{tenant.user_id}.json"


def _normalize_profile(profile: dict[str, Any], tenant: TenantContext) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "profile_version": int(profile.get("profile_version") or PROFILE_VERSION),
        "user_id": tenant.user_id,
        "namespace": tenant.namespace,
    }
    for field in TEXT_PROFILE_FIELDS:
        default_value = str(DEFAULT_PROFILE.get(field, ""))
        normalized[field] = _string_or_empty(profile.get(field)) or default_value
    for field in LIST_PROFILE_FIELDS:
        field_value = profile.get(field)
        normalized[field] = _merge_unique([], field_value if isinstance(field_value, list) else [])
    normalized["last_update_reason"] = _string_or_none(profile.get("last_update_reason"))
    normalized["updated_at"] = _string_or_none(profile.get("updated_at"))
    return normalized


def _merge_unique(existing: list[str], incoming: list[Any]) -> list[str]:
    merged: list[str] = []
    seen = set()
    for item in [*existing, *incoming]:
        text = _string_or_empty(item)
        if not text:
            continue
        key = _topic_key(text)
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return merged


def _topic_key(topic: str) -> str:
    return topic.strip().casefold()


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _string_or_none(value: Any) -> str | None:
    text = _string_or_empty(value)
    return text or None


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
