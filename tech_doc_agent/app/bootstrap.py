from langgraph.checkpoint.redis import RedisSaver

from tech_doc_agent.app.composition import build_application_graph
from tech_doc_agent.app.core.errors import safe_error_fields
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings, get_settings
from tech_doc_agent.app.infrastructure.persistence.approval_repository import RedisApprovalRepository
from tech_doc_agent.app.runtime.chat_runtime import ChatRuntime
from tech_doc_agent.app.runtime.lifecycle import RuntimeLifecycle
from tech_doc_agent.app.agents.identity import build_runtime_execution_identity
from tech_doc_agent.app.services.resources import AppResources


def build_runtime_lifecycle(settings: Settings) -> RuntimeLifecycle:
    return RuntimeLifecycle(
        settings=settings,
        resource_factory=AppResources.create,
        checkpointer_context_factory=RedisSaver.from_conn_string,
        graph_factory=build_application_graph,
    )


def build_chat_runtime(settings: Settings | None = None) -> ChatRuntime:
    """Compose the production runtime without making ChatRuntime itself network-aware."""

    resolved_settings = settings if settings is not None else get_settings()
    approval_repository = RedisApprovalRepository.from_url(
        resolved_settings.REDIS_URL,
        ttl_seconds=resolved_settings.GUARDRAIL_APPROVAL_TTL_SECONDS,
    )
    lifecycle = build_runtime_lifecycle(resolved_settings)
    try:
        return ChatRuntime(
            settings=resolved_settings,
            lifecycle=lifecycle,
            approval_repository=approval_repository,
            execution_identity_factory=build_runtime_execution_identity,
        )
    except Exception:
        try:
            approval_repository.close()
        except Exception as close_error:
            log_event(
                "runtime.construction.cleanup.error",
                **safe_error_fields(
                    close_error,
                    dependency="approval_repository",
                ),
            )
        raise
