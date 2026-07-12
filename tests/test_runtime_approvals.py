from contextlib import nullcontext

from tech_doc_agent.app.application.approval_service import ApprovalService
from tech_doc_agent.app.core.settings import Settings
from tech_doc_agent.app.core.tenant import TenantContext
from tech_doc_agent.app.infrastructure.persistence.in_memory_approval_repository import (
    InMemoryApprovalRepository,
)
from tech_doc_agent.app.runtime.approvals import ApprovalService as RuntimeApprovalService
from tech_doc_agent.app.runtime.telemetry import RuntimeOperationTelemetry
from tests.fakes.chat_runtime import build_test_chat_runtime


def test_approval_service_isolates_tenants_and_resolves_once():
    events = []
    repository = InMemoryApprovalRepository()
    service = ApprovalService(
        repository,
        event_logger=lambda event, **fields: events.append((event, fields)),
    )

    request_a = service.request_guardrail_approval(
        "same-session",
        "message-a",
        source="chat.message",
        risk_level="medium",
        findings=["rule-a"],
        user_id="user-a",
        namespace="docs",
    )
    request_b = service.request_guardrail_approval(
        "same-session",
        "message-b",
        source="chat.message",
        risk_level="medium",
        findings=["rule-b"],
        user_id="user-b",
        namespace="docs",
    )

    assert service.get_pending_guardrail_approval(
        "same-session",
        user_id="user-a",
        namespace="docs",
    ) == request_a
    assert service.get_pending_guardrail_approval(
        "same-session",
        user_id="user-b",
        namespace="docs",
    ) == request_b

    assert service.pop_pending_guardrail_approval(
        "same-session",
        user_id="user-a",
        namespace="docs",
    ) == request_a
    assert service.pop_pending_guardrail_approval(
        "same-session",
        user_id="user-a",
        namespace="docs",
    ) is None
    assert service.has_pending_guardrail_approval(
        "same-session",
        user_id="user-b",
        namespace="docs",
    )
    assert [event for event, _ in events] == [
        "guardrail.approval.requested",
        "guardrail.approval.requested",
    ]


def test_runtime_approval_service_compatibility_wrapper_preserves_rejection_part():
    service = RuntimeApprovalService(
        InMemoryApprovalRepository(),
        event_logger=lambda *args, **kwargs: None,
    )
    pending = service.request_guardrail_approval(
        "session-compat",
        "message",
        source="chat.message",
        risk_level="medium",
        findings=["rule"],
        user_id="user-a",
        namespace="docs",
    )

    stream_type, update = service.rejection_part(pending, "not now")

    message = update["guardrail"]["messages"][0]
    assert stream_type == "updates"
    assert message.name == "guardrail"
    assert "not now" in message.content


def test_chat_runtime_accepts_an_injected_approval_repository():
    repository = InMemoryApprovalRepository()
    runtime = build_test_chat_runtime(approval_repository=repository)

    request = runtime.request_guardrail_approval(
        "session-1",
        "message",
        source="chat.message",
        risk_level="medium",
        findings=["rule"],
        user_id="user-a",
        namespace="docs",
    )

    assert repository.get("user-a:docs:session-1") == request


def test_chat_runtime_closes_the_injected_approval_repository():
    class ClosingRepository(InMemoryApprovalRepository):
        def __init__(self):
            super().__init__()
            self.closed = False

        def close(self):
            self.closed = True

    repository = ClosingRepository()
    runtime = build_test_chat_runtime(
        approval_repository=repository,
        settings=Settings(LANGFUSE_ENABLED=False),
    )

    runtime.__exit__(None, None, None)

    assert repository.closed is True


def test_runtime_telemetry_keeps_async_marker_out_of_sync_events():
    events = []
    timer_calls = []
    clock_values = iter([10.0, 10.25, 20.0, 20.5])
    telemetry = RuntimeOperationTelemetry(
        event_logger=lambda event, **fields: events.append((event, fields)),
        timer_factory=lambda name, **fields: (
            timer_calls.append((name, fields)) or nullcontext()
        ),
        clock=lambda: next(clock_values),
    )
    tenant = TenantContext(user_id="user-a", namespace="docs")

    sync_trace = telemetry.start_chat(
        "session-sync",
        tenant,
        message_length=7,
        async_runtime=False,
    )
    with telemetry.stream_timer(sync_trace):
        pass
    telemetry.finish(sync_trace, pending_interrupt=False)

    async_trace = telemetry.start_approval(
        "session-async",
        tenant,
        approved=True,
        async_runtime=True,
    )
    with telemetry.stream_timer(async_trace):
        pass
    telemetry.finish(async_trace, pending_interrupt=True)

    assert events[0] == (
        "chat.request.started",
        {
            "session_id": "session-sync",
            "user_id": "user-a",
            "namespace": "docs",
            "message_length": 7,
        },
    )
    assert events[1][0] == "chat.request.finished"
    assert events[1][1]["elapsed_ms"] == 250.0
    assert "async_runtime" not in events[1][1]
    assert events[2][1]["async_runtime"] is True
    assert events[3][0] == "chat.approval.interrupted"
    assert events[3][1]["elapsed_ms"] == 500.0
    assert timer_calls == [
        ("graph.stream", {"phase": "chat"}),
        ("graph.stream.thread", {"phase": "approval", "approved": True}),
    ]
