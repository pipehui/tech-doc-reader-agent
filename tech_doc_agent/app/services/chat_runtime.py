from time import sleep
from typing import Any

from langgraph.checkpoint.redis import RedisSaver
from langgraph.types import StateSnapshot
from redis.exceptions import BusyLoadingError

from tech_doc_agent.app.core.langfuse_tracing import shutdown_langfuse
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.graph import build_multi_agentic_graph
from tech_doc_agent.app.runtime import (
    ApprovalRepository,
    ApprovalService,
    GraphExecutionService,
    GuardrailApprovalRequest,
    InMemoryApprovalRepository,
    SessionConfigFactory,
    SessionQueryService,
)
from tech_doc_agent.app.services.resources import AppResources, reset_app_resources, set_app_resources


def _is_retryable_redis_startup_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return isinstance(exc, BusyLoadingError) or "redis is loading" in message or "loading the dataset" in message


class ChatRuntime:
    def __init__(self, approval_repository: ApprovalRepository | None = None) -> None:
        self.settings = get_settings()
        self._checkpointer_cm: Any | None = None
        self.checkpointer: Any | None = None
        self.graph: Any | None = None
        self.resources: Any | None = None
        self._approval_repository = (
            approval_repository
            if approval_repository is not None
            else InMemoryApprovalRepository()
        )
        self._approval_service = ApprovalService(self._approval_repository)
        self._session_queries = SessionQueryService(
            graph_provider=self._require_graph,
            config_builder=self.build_config,
            pending_guardrail_checker=self._approval_service.has_pending_guardrail_approval,
        )
        self._execution = GraphExecutionService(
            settings_provider=lambda: self.settings,
            graph_provider=self._require_graph,
            config_builder=self.build_config,
            session_queries=self._session_queries,
            approvals=self._approval_service,
        )

    def __enter__(self):
        try:
            self.resources = AppResources.create(self.settings)
            set_app_resources(self.resources)
            self._setup_checkpointer_with_retry()
            self.graph = build_multi_agentic_graph(self.checkpointer)
            return self
        except Exception:
            self._close_checkpointer()
            reset_app_resources()
            raise

    def __exit__(self, exc_type, exc, tb):
        shutdown_langfuse(self.settings)

        try:
            self._close_checkpointer(exc_type, exc, tb)
        finally:
            reset_app_resources()

    def _setup_checkpointer_with_retry(self) -> None:
        max_attempts = max(1, int(self.settings.REDIS_SETUP_MAX_ATTEMPTS))
        retry_seconds = max(0.0, float(self.settings.REDIS_SETUP_RETRY_SECONDS))

        for attempt in range(1, max_attempts + 1):
            self._checkpointer_cm = RedisSaver.from_conn_string(self.settings.REDIS_URL)
            try:
                self.checkpointer = self._checkpointer_cm.__enter__()
                self.checkpointer.setup()
                if attempt > 1:
                    log_event("redis.checkpointer.setup.ready", attempt=attempt)
                return
            except Exception as exc:
                self._close_checkpointer()
                if attempt >= max_attempts or not _is_retryable_redis_startup_error(exc):
                    raise
                log_event(
                    "redis.checkpointer.setup.retry",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    retry_seconds=retry_seconds,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                sleep(retry_seconds)

    def _close_checkpointer(self, exc_type=None, exc=None, tb=None) -> None:
        if self._checkpointer_cm is not None:
            self._checkpointer_cm.__exit__(exc_type, exc, tb)
        self._checkpointer_cm = None
        self.checkpointer = None

    def _require_graph(self) -> Any:
        if self.graph is None:
            raise RuntimeError("ChatRuntime graph is not initialized.")
        return self.graph

    def build_config(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
        operation: str = "state",
        with_callbacks: bool = False,
    ) -> dict:
        return SessionConfigFactory(self.settings).build(
            session_id,
            user_id=user_id,
            namespace=namespace,
            operation=operation,
            with_callbacks=with_callbacks,
        )

    def request_guardrail_approval(
        self,
        session_id: str,
        user_input: str,
        *,
        source: str,
        risk_level: str,
        findings: list[str] | tuple[str, ...],
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> GuardrailApprovalRequest:
        return self._approval_service.request_guardrail_approval(
            session_id,
            user_input,
            source=source,
            risk_level=risk_level,
            findings=findings,
            user_id=user_id,
            namespace=namespace,
        )

    def get_pending_guardrail_approval(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> GuardrailApprovalRequest | None:
        return self._approval_service.get_pending_guardrail_approval(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )

    def has_pending_guardrail_approval(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        return self._approval_service.has_pending_guardrail_approval(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )

    def stream_user_message(
        self,
        session_id: str,
        user_input: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        yield from self._execution.stream_user_message(
            session_id,
            user_input,
            user_id=user_id,
            namespace=namespace,
        )

    async def astream_user_message(
        self,
        session_id: str,
        user_input: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        async for part in self._execution.astream_user_message(
            session_id,
            user_input,
            user_id=user_id,
            namespace=namespace,
        ):
            yield part

    def has_pending_interrupt(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        return self._execution.has_pending_interrupt(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )

    async def ahas_pending_interrupt(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> bool:
        return await self._execution.ahas_pending_interrupt(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )

    def stream_approval(
        self,
        session_id: str,
        approved: bool,
        feedback: str = "",
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        yield from self._execution.stream_approval(
            session_id,
            approved,
            feedback=feedback,
            user_id=user_id,
            namespace=namespace,
        )

    async def astream_approval(
        self,
        session_id: str,
        approved: bool,
        feedback: str = "",
        user_id: str | None = None,
        namespace: str | None = None,
    ):
        async for part in self._execution.astream_approval(
            session_id,
            approved,
            feedback=feedback,
            user_id=user_id,
            namespace=namespace,
        ):
            yield part

    def get_snapshot(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> StateSnapshot:
        return self._session_queries.get_snapshot(session_id, user_id=user_id, namespace=namespace)

    async def aget_snapshot(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> StateSnapshot:
        return await self._session_queries.aget_snapshot(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )
    
    def get_history(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict:
        return self._session_queries.get_history(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )
    
    def get_history_view(
        self,
        session_id: str,
        include_tools: bool = False,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict:
        return self._session_queries.get_history_view(
            session_id,
            include_tools=include_tools,
            user_id=user_id,
            namespace=namespace,
        )

    def get_session_state(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict:
        return self._session_queries.get_session_state(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )

    async def aget_session_state(
        self,
        session_id: str,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict:
        return await self._session_queries.aget_session_state(
            session_id,
            user_id=user_id,
            namespace=namespace,
        )
