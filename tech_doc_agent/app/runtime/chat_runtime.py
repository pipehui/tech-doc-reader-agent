from typing import Any

from langgraph.types import StateSnapshot

from tech_doc_agent.app.application.approval_models import (
    ApprovalRepository,
    GuardrailApprovalRequest,
)
from tech_doc_agent.app.core.errors import safe_error_fields
from tech_doc_agent.app.core.langfuse_tracing import shutdown_langfuse
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.runtime.approvals import (
    ApprovalService,
)
from tech_doc_agent.app.runtime.config import SessionConfigFactory
from tech_doc_agent.app.runtime.execution import GraphExecutionService
from tech_doc_agent.app.runtime.identity import (
    RuntimeExecutionIdentityFactory,
    RuntimeExecutionIdentityPort,
)
from tech_doc_agent.app.runtime.lifecycle import RuntimeLifecycle
from tech_doc_agent.app.runtime.sessions import SessionQueryService


class ChatRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        lifecycle: RuntimeLifecycle,
        approval_repository: ApprovalRepository,
        execution_identity_factory: RuntimeExecutionIdentityFactory,
        execution_identity: RuntimeExecutionIdentityPort | None = None,
    ) -> None:
        self._settings = settings
        self._execution_identity_factory = execution_identity_factory
        self._execution_identity_override = execution_identity is not None
        self._execution_identity = (
            execution_identity or execution_identity_factory(settings)
        )
        self._lifecycle = lifecycle
        self._approval_repository = approval_repository
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

    @property
    def settings(self) -> Settings:
        return self._settings

    @settings.setter
    def settings(self, value: Settings) -> None:
        self._settings = value
        if not self._execution_identity_override:
            self._execution_identity = self._execution_identity_factory(value)

    @property
    def execution_identity(self) -> RuntimeExecutionIdentityPort:
        return self._execution_identity

    @property
    def resources(self) -> Any | None:
        return self._lifecycle.resources

    @resources.setter
    def resources(self, value: Any | None) -> None:
        self._lifecycle.resources = value

    @property
    def checkpointer(self) -> Any | None:
        return self._lifecycle.checkpointer

    @checkpointer.setter
    def checkpointer(self, value: Any | None) -> None:
        self._lifecycle.checkpointer = value

    @property
    def graph(self) -> Any | None:
        return self._lifecycle.graph

    @graph.setter
    def graph(self, value: Any | None) -> None:
        self._lifecycle.graph = value

    def __enter__(self):
        try:
            self._lifecycle.settings = self.settings
            self._lifecycle.start()
            return self
        except Exception:
            self._close_approval_repository()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            shutdown_langfuse(self.settings)
        finally:
            try:
                self._lifecycle.close(exc_type, exc, tb)
            finally:
                self._close_approval_repository()

    def _close_approval_repository(self) -> None:
        try:
            self._approval_repository.close()
        except Exception as exc:
            log_event(
                "approval.repository.close.error",
                **safe_error_fields(exc, dependency="approval_repository"),
            )

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
        request_started_monotonic: float | None = None,
    ) -> dict:
        return SessionConfigFactory(
            self.settings,
            execution_identity_metadata=self.execution_identity.to_payload(),
        ).build(
            session_id,
            user_id=user_id,
            namespace=namespace,
            operation=operation,
            with_callbacks=with_callbacks,
            request_started_monotonic=request_started_monotonic,
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
        request_started_monotonic: float | None = None,
    ):
        yield from self._execution.stream_user_message(
            session_id,
            user_input,
            user_id=user_id,
            namespace=namespace,
            request_started_monotonic=request_started_monotonic,
        )

    async def astream_user_message(
        self,
        session_id: str,
        user_input: str,
        user_id: str | None = None,
        namespace: str | None = None,
        request_started_monotonic: float | None = None,
    ):
        async for part in self._execution.astream_user_message(
            session_id,
            user_input,
            user_id=user_id,
            namespace=namespace,
            request_started_monotonic=request_started_monotonic,
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
        request_started_monotonic: float | None = None,
    ):
        yield from self._execution.stream_approval(
            session_id,
            approved,
            feedback=feedback,
            user_id=user_id,
            namespace=namespace,
            request_started_monotonic=request_started_monotonic,
        )

    async def astream_approval(
        self,
        session_id: str,
        approved: bool,
        feedback: str = "",
        user_id: str | None = None,
        namespace: str | None = None,
        request_started_monotonic: float | None = None,
    ):
        async for part in self._execution.astream_approval(
            session_id,
            approved,
            feedback=feedback,
            user_id=user_id,
            namespace=namespace,
            request_started_monotonic=request_started_monotonic,
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

    async def aget_history_view(
        self,
        session_id: str,
        include_tools: bool = False,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> dict:
        return await self._session_queries.aget_history_view(
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
