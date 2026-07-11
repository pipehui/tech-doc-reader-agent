from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.infrastructure.persistence.approval_repository import RedisApprovalRepository
from tech_doc_agent.app.services.chat_runtime import ChatRuntime


def build_chat_runtime(settings: Settings | None = None) -> ChatRuntime:
    """Compose the production runtime without making ChatRuntime itself network-aware."""

    resolved_settings = settings if settings is not None else get_settings()
    approval_repository = RedisApprovalRepository.from_url(
        resolved_settings.REDIS_URL,
        ttl_seconds=resolved_settings.GUARDRAIL_APPROVAL_TTL_SECONDS,
    )
    return ChatRuntime(
        approval_repository=approval_repository,
        settings=resolved_settings,
    )
