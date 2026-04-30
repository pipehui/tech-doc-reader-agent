import json
from typing import Optional

from langchain_core.tools import tool

from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.core.tenant import current_tenant
from tech_doc_agent.app.services.user_profile import (
    get_user_profile,
    update_user_profile as update_user_profile_data,
)


@tool
def read_user_profile() -> str:
    """
    读取当前用户的长期用户画像。
    画像记录的是稳定偏好和能力信息，例如经验水平、解释风格、解释深度、熟悉主题和薄弱主题。
    它不是本轮学习总结，也不是学习轨迹 memory。
    """
    tenant = current_tenant()
    profile = get_user_profile(
        tenant.user_id,
        tenant.namespace,
        settings=get_settings(),
    )
    return json.dumps(profile, ensure_ascii=False)


@tool
def update_user_profile(
    experience_level: Optional[str] = None,
    explanation_style: Optional[str] = None,
    depth: Optional[str] = None,
    language: Optional[str] = None,
    known_topics: Optional[list[str]] = None,
    weak_topics: Optional[list[str]] = None,
    resolved_weak_topics: Optional[list[str]] = None,
    notes: Optional[str] = None,
    evidence: Optional[str] = None,
) -> str:
    """
    更新当前用户的长期用户画像。
    只有当用户明确要求更新能力、偏好或个人画像时才可以调用。
    写入前应尽量先读取当前画像、学习记录和长期学习轨迹记忆作为依据。
    """
    tenant = current_tenant()
    profile = update_user_profile_data(
        tenant.user_id,
        tenant.namespace,
        experience_level=experience_level,
        explanation_style=explanation_style,
        depth=depth,
        language=language,
        known_topics=known_topics,
        weak_topics=weak_topics,
        resolved_weak_topics=resolved_weak_topics,
        notes=notes,
        evidence=evidence,
        settings=get_settings(),
    )
    return json.dumps(profile, ensure_ascii=False)
