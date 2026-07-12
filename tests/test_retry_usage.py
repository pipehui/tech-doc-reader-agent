import copy

import pytest

from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.retry import build_retry_executor
from tech_doc_agent.app.core.retry_usage import (
    RetryUsage,
    RetryUsageLedger,
    capture_retry_usage,
    retry_usage_delta_payload,
)
from tech_doc_agent.app.core.settings import Settings


def _usage(
    *,
    dependency: str = "embedding",
    attempts: int = 2,
    retries: int = 1,
    outcome: str = "succeeded",
) -> RetryUsage:
    return RetryUsage(
        operation=f"{dependency}.request",
        dependency=dependency,
        tool="read_docs",
        idempotent=True,
        attempts=attempts,
        retries=retries,
        waited_seconds=0.25,
        outcome=outcome,
        reason="completed" if outcome == "succeeded" else "max_attempts_exhausted",
        error_code=None if outcome == "succeeded" else "dependency_timeout",
    )


def test_retry_usage_ledger_round_trips_and_recomputes_summary():
    ledger = RetryUsageLedger().record(
        (
            _usage(),
            _usage(
                dependency="duckduckgo",
                attempts=3,
                retries=2,
                outcome="exhausted",
            ),
        )
    )
    state = ledger.to_state()

    assert RetryUsageLedger.from_state(state) == ledger
    assert state["summary"] == {
        "operations": 2,
        "attempts": 5,
        "retries": 3,
        "waited_seconds": 0.5,
        "recovered_operations": 1,
        "exhausted_operations": 1,
        "failed_operations": 0,
        "dependencies": {
            "duckduckgo": {
                "operations": 1,
                "attempts": 3,
                "retries": 2,
                "waited_seconds": 0.25,
            },
            "embedding": {
                "operations": 1,
                "attempts": 2,
                "retries": 1,
                "waited_seconds": 0.25,
            },
        },
    }


def test_retry_usage_ledger_rejects_tampered_summary():
    state = RetryUsageLedger().record((_usage(),)).to_state()
    tampered = copy.deepcopy(state)
    tampered["summary"]["attempts"] = 999

    with pytest.raises(ValidationError) as exc_info:
        RetryUsageLedger.from_state(tampered)

    assert exc_info.value.code == "retry_usage_ledger_invalid"


@pytest.mark.parametrize(
    "override",
    [
        {"operation": ""},
        {"attempts": -1},
        {"attempts": 1, "retries": 1},
        {"waited_seconds": float("inf")},
        {"outcome": "succeeded", "attempts": 0, "retries": 0},
    ],
)
def test_retry_usage_rejects_invalid_values(override):
    values = _usage().to_payload()
    values.update(override)

    with pytest.raises(ValidationError):
        RetryUsage.from_payload(values)


def test_built_retry_executor_records_into_request_local_capture():
    calls = 0
    executor = build_retry_executor(
        Settings(
            TRANSPORT_RETRY_MAX_ATTEMPTS=2,
            TRANSPORT_RETRY_INITIAL_DELAY_SECONDS=0,
            TRANSPORT_RETRY_MAX_DELAY_SECONDS=0,
            TRANSPORT_RETRY_JITTER_RATIO=0,
        )
    )

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("private provider endpoint")
        return "ok"

    with capture_retry_usage() as collector:
        assert executor.run(
            operation,
            operation_name="embedding.create",
            dependency="embedding",
            tool="read_docs",
            idempotent=True,
        ) == "ok"

    usages = collector.snapshot()
    assert len(usages) == 1
    assert usages[0].attempts == 2
    assert usages[0].retries == 1
    assert usages[0].outcome == "succeeded"
    assert "private provider endpoint" not in str(usages[0].to_payload())

    with capture_retry_usage() as next_collector:
        pass
    assert next_collector.snapshot() == ()


def test_retry_usage_delta_distinguishes_reset_from_operations():
    reset = retry_usage_delta_payload((), kind="reset")
    operations = retry_usage_delta_payload((_usage(),))

    assert reset["kind"] == "reset"
    assert reset["summary"]["operations"] == 0
    assert operations["kind"] == "operations"
    assert operations["summary"]["attempts"] == 2
