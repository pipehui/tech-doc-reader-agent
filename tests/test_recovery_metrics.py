from langchain_core.messages import ToolMessage

from evals.recovery_metrics import summarize_recovery_events
from tech_doc_agent.app.core.retry import RetryExecutor, RetryPolicy
import tech_doc_agent.app.graph.reflection as reflection_module
from tech_doc_agent.app.graph.reflection import apply_reflection_policy
from tech_doc_agent.app.graph.specs import ReflectionPolicy


def test_recovery_metrics_keep_transport_argument_repair_and_task_success_separate():
    metrics = summarize_recovery_events(
        [
            {
                "event": "retry.final",
                "outcome": "succeeded",
                "attempts": 3,
                "retries": 2,
            },
            {
                "event": "retry.final",
                "outcome": "exhausted",
                "attempts": 3,
                "retries": 2,
            },
            {"event": "reflection.started", "reflection_round": 1},
            {"event": "reflection.terminated", "reason": "max_rounds_exhausted"},
        ],
        task_success=False,
    )

    assert metrics.to_payload() == {
        "transport_recovered_operations": 1,
        "transport_exhausted_operations": 1,
        "transport_retries": 4,
        "argument_repair_rounds": 1,
        "reflection_terminations": 1,
        "task_success": False,
        "additional_attempts": 5,
    }


def test_recovery_metrics_ignore_malformed_retry_counts():
    metrics = summarize_recovery_events(
        [
            {"event": "retry.final", "outcome": "succeeded", "retries": True},
            {"event": "retry.final", "outcome": "succeeded", "retries": -2},
            {"event": "unrelated", "retries": 100},
        ],
        task_success=True,
    )

    assert metrics.transport_retries == 0
    assert metrics.transport_recovered_operations == 0
    assert metrics.task_success is True
    assert metrics.additional_attempts == 0


def test_fault_injection_metrics_consume_real_retry_and_reflection_events(monkeypatch):
    events = []

    def record(event, **fields):
        events.append({"event": event, **fields})

    calls = 0
    retry_executor = RetryExecutor(
        RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        sleeper=lambda delay: None,
        event_logger=record,
    )

    def transient_operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("private endpoint")
        return "ok"

    assert retry_executor.run(
        transient_operation,
        operation_name="fault.transport",
        dependency="provider",
        idempotent=True,
    ) == "ok"

    monkeypatch.setattr(reflection_module, "log_event", record)
    error_payload = {
        "status": "error",
        "code": "validation_error",
        "retryable": False,
        "safe_message": "The request was invalid.",
        "dependency": None,
        "tool": "read_docs",
        "cause_type": "ValidationError",
    }
    apply_reflection_policy(
        {
            "messages": [],
            "dialog_state": ["parser"],
            "reflection_rounds_used": 0,
        },
        {
            "messages": [
                ToolMessage(
                    content="safe",
                    tool_call_id="call-1",
                    status="error",
                    artifact={"error": error_payload},
                )
            ]
        },
        ReflectionPolicy(max_rounds=1),
    )

    metrics = summarize_recovery_events(events, task_success=True)

    assert metrics.transport_recovered_operations == 1
    assert metrics.transport_retries == 1
    assert metrics.argument_repair_rounds == 1
    assert metrics.task_success is True
    assert metrics.additional_attempts == 2
    assert "private endpoint" not in str(events)
