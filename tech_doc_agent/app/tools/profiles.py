import json
from dataclasses import dataclass
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from tech_doc_agent.app.core.tenant import tenant_from_config
from tech_doc_agent.app.tools.dependencies import ToolDependencies


@dataclass(frozen=True, slots=True)
class ProfileTools:
    read_user_profile: BaseTool
    update_user_profile: BaseTool


def build_profile_tools(dependencies: ToolDependencies) -> ProfileTools:
    @tool
    def read_user_profile(config: RunnableConfig) -> str:
        """
        读取当前用户的长期用户画像。
        画像记录的是稳定偏好和能力信息，例如经验水平、解释风格、解释深度、熟悉主题和薄弱主题。
        它不是本轮学习总结，也不是学习轨迹 memory。
        """

        tenant = tenant_from_config(config)
        profile = dependencies.profile_service.get_profile(
            user_id=tenant.user_id,
            namespace=tenant.namespace,
        )
        return json.dumps(profile, ensure_ascii=False)

    @tool("update_user_profile")
    def update_user_profile_tool(
        config: RunnableConfig,
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

        tenant = tenant_from_config(config)
        profile = dependencies.profile_service.update_profile(
            user_id=tenant.user_id,
            namespace=tenant.namespace,
            experience_level=experience_level,
            explanation_style=explanation_style,
            depth=depth,
            language=language,
            known_topics=known_topics,
            weak_topics=weak_topics,
            resolved_weak_topics=resolved_weak_topics,
            notes=notes,
            evidence=evidence,
        )
        return json.dumps(profile, ensure_ascii=False)

    return ProfileTools(
        read_user_profile=read_user_profile,
        update_user_profile=update_user_profile_tool,
    )
