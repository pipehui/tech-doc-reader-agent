from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tech_doc_agent.app.application.approval_models import ApprovalRepository
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.infrastructure.persistence.in_memory_approval_repository import (
    InMemoryApprovalRepository,
)
from tech_doc_agent.app.runtime.chat_runtime import ChatRuntime
from tech_doc_agent.app.runtime.identity import RuntimeExecutionIdentityPort
from tech_doc_agent.app.runtime.lifecycle import RuntimeLifecycle
from tech_doc_agent.app.services.assistants.identity import (
    build_runtime_execution_identity,
)


def build_test_chat_runtime(
    *,
    settings: Settings | None = None,
    lifecycle: RuntimeLifecycle | None = None,
    approval_repository: ApprovalRepository | None = None,
    execution_identity: RuntimeExecutionIdentityPort | None = None,
    execution_identity_factory: Callable[
        [Settings],
        RuntimeExecutionIdentityPort,
    ] = build_runtime_execution_identity,
) -> ChatRuntime:
    resolved_settings = (
        settings
        if settings is not None
        else lifecycle.settings
        if lifecycle is not None
        else Settings()
    )
    return ChatRuntime(
        settings=resolved_settings,
        lifecycle=(
            lifecycle
            if lifecycle is not None
            else _inert_lifecycle(resolved_settings)
        ),
        approval_repository=(
            approval_repository
            if approval_repository is not None
            else InMemoryApprovalRepository()
        ),
        execution_identity_factory=execution_identity_factory,
        execution_identity=execution_identity,
    )


def _inert_lifecycle(settings: Settings) -> RuntimeLifecycle:
    return RuntimeLifecycle(
        settings=settings,
        resource_factory=_unexpected_lifecycle_start,
        checkpointer_context_factory=_unexpected_lifecycle_start,
        graph_factory=_unexpected_lifecycle_start,
    )


def _unexpected_lifecycle_start(*args: Any) -> Any:
    del args
    raise AssertionError(
        "The inert test runtime cannot be entered; inject a RuntimeLifecycle."
    )


__all__ = ["build_test_chat_runtime"]
