from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from tech_doc_agent.app.core.retry import RetryExecutor, build_retry_executor
from tech_doc_agent.app.core.settings import Settings

from .identity import build_model_route_identity


def _secret_or_placeholder(value: str) -> SecretStr:
    return SecretStr(value or "not-set")


def _base_url_or_none(value: str) -> str | None:
    return value or None


@dataclass(frozen=True, slots=True)
class AssistantModelProvider:
    primary: Any
    backup: Any | None = None
    retry_executor: RetryExecutor | None = None
    provider_id: str = "openai_compatible"
    primary_model_id: str | None = None
    backup_model_id: str | None = None

    def bind_tools(self, tools: list[Any], *, parallel_tool_calls: bool = False) -> Any:
        primary_bound = self.primary.bind_tools(
            tools,
            parallel_tool_calls=parallel_tool_calls,
        )
        if self.backup is None:
            return primary_bound

        backup_bound = self.backup.bind_tools(
            tools,
            parallel_tool_calls=parallel_tool_calls,
        )
        return primary_bound.with_fallbacks([backup_bound])


def build_assistant_model_provider(settings: Settings) -> AssistantModelProvider:
    route = build_model_route_identity(settings)
    primary = ChatOpenAI(
        model=route.primary_model_id,
        api_key=_secret_or_placeholder(settings.OPENAI_API_KEY),
        base_url=_base_url_or_none(settings.OPENAI_BASE_URL),
        temperature=0,
        max_retries=0,
    )

    backup = None
    if route.backup_model_id is not None:
        backup = ChatOpenAI(
            model=route.backup_model_id,
            api_key=_secret_or_placeholder(settings.BACKUP_API_KEY),
            base_url=_base_url_or_none(settings.BACKUP_API_BASE),
            temperature=0,
            max_retries=0,
        )

    return AssistantModelProvider(
        primary=primary,
        backup=backup,
        retry_executor=build_retry_executor(settings),
        provider_id=route.provider_id,
        primary_model_id=route.primary_model_id,
        backup_model_id=route.backup_model_id,
    )
