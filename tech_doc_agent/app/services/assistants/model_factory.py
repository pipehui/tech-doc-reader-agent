from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from tech_doc_agent.app.core.settings import Settings


def _secret_or_placeholder(value: str) -> SecretStr:
    return SecretStr(value or "not-set")


def _base_url_or_none(value: str) -> str | None:
    return value or None


@dataclass(frozen=True, slots=True)
class AssistantModelProvider:
    primary: Any
    backup: Any | None = None

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
    primary = ChatOpenAI(
        model=settings.PRIMARY_MODEL or "gpt-4o-mini",
        api_key=_secret_or_placeholder(settings.OPENAI_API_KEY),
        base_url=_base_url_or_none(settings.OPENAI_BASE_URL),
        temperature=0,
    )

    backup = None
    if settings.BACKUP_MODEL and settings.BACKUP_API_KEY:
        backup = ChatOpenAI(
            model=settings.BACKUP_MODEL,
            api_key=_secret_or_placeholder(settings.BACKUP_API_KEY),
            base_url=_base_url_or_none(settings.BACKUP_API_BASE),
            temperature=0,
        )

    return AssistantModelProvider(primary=primary, backup=backup)
