import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tech_doc_agent.app.core.errors import PermissionDenied, RateLimited, Timeout, ValidationError
from tech_doc_agent.app.core.retry import RetryExecutor, RetryPolicy


class ProviderRateLimit(RuntimeError):
    status_code = 429

    def __init__(self, retry_after: str):
        super().__init__("private provider response")
        self.response = SimpleNamespace(headers={"Retry-After": retry_after})


class ProviderUnavailable(RuntimeError):
    status_code = 503


def _recording_executor(policy, *, sleeps=None, async_sleeps=None, events=None, usages=None):
    sleeps = sleeps if sleeps is not None else []
    async_sleeps = async_sleeps if async_sleeps is not None else []
    events = events if events is not None else []
    usages = usages if usages is not None else []

    async def async_sleep(delay):
        async_sleeps.append(delay)

    return RetryExecutor(
        policy,
        sleeper=sleeps.append,
        async_sleeper=async_sleep,
        random_value=lambda: 0.5,
        event_logger=lambda event, **fields: events.append((event, fields)),
        usage_observer=usages.append,
    )


def test_retry_executor_recovers_with_finite_exponential_backoff_and_usage():
    calls = 0
    sleeps = []
    events = []
    usages = []
    executor = _recording_executor(
        RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=1,
            max_delay_seconds=10,
            backoff_multiplier=2,
            jitter_ratio=0,
        ),
        sleeps=sleeps,
        events=events,
        usages=usages,
    )

    def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("private URL and bearer token")
        return "recovered"

    result = executor.run(
        operation,
        operation_name="test.read",
        dependency="provider",
        idempotent=True,
    )

    assert result == "recovered"
    assert calls == 3
    assert sleeps == [1.0, 2.0]
    assert [event for event, _ in events] == [
        "retry.attempt",
        "retry.scheduled",
        "retry.attempt",
        "retry.scheduled",
        "retry.attempt",
        "retry.final",
    ]
    assert usages[0].attempts == 3
    assert usages[0].retries == 2
    assert usages[0].waited_seconds == 3.0
    assert usages[0].outcome == "succeeded"
    assert "bearer token" not in str(events)


@pytest.mark.parametrize(
    ("provider_error", "expected_error"),
    [
        (ValueError("private malformed payload"), ValidationError),
        (PermissionError("private credential path"), PermissionDenied),
    ],
)
def test_retry_executor_does_not_retry_validation_or_permission_failures(
    provider_error,
    expected_error,
):
    calls = 0
    sleeps = []
    events = []
    executor = _recording_executor(
        RetryPolicy(max_attempts=3),
        sleeps=sleeps,
        events=events,
    )

    def operation():
        nonlocal calls
        calls += 1
        raise provider_error

    with pytest.raises(expected_error):
        executor.run(
            operation,
            operation_name="test.validation",
            dependency="provider",
            idempotent=True,
        )

    assert calls == 1
    assert sleeps == []
    assert events[-1][1]["reason"] == "non_retryable"
    assert str(provider_error) not in str(events)


def test_retry_executor_retries_server_side_5xx_failure():
    calls = 0
    executor = _recording_executor(
        RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        )
    )

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderUnavailable("private upstream body")
        return "ok"

    assert executor.run(
        operation,
        operation_name="test.server_error",
        dependency="provider",
        idempotent=True,
    ) == "ok"
    assert calls == 2


def test_retry_executor_never_retries_non_idempotent_operation():
    calls = 0
    sleeps = []
    events = []
    executor = _recording_executor(
        RetryPolicy(max_attempts=3),
        sleeps=sleeps,
        events=events,
    )

    def sensitive_write():
        nonlocal calls
        calls += 1
        raise TimeoutError("commit outcome is ambiguous")

    with pytest.raises(Timeout):
        executor.run(
            sensitive_write,
            operation_name="test.sensitive_write",
            dependency="repository",
            idempotent=False,
        )

    assert calls == 1
    assert sleeps == []
    assert events[-1][1]["reason"] == "non_idempotent"


def test_before_attempt_guard_is_not_counted_as_provider_retry():
    calls = 0
    usages = []
    executor = _recording_executor(
        RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        usages=usages,
    )

    def before_attempt(attempt):
        if attempt == 3:
            raise RateLimited(
                code="local_quota_reached",
                retryable=False,
                dependency="provider",
            )

    def operation():
        nonlocal calls
        calls += 1
        raise TimeoutError("private endpoint")

    with pytest.raises(RateLimited):
        executor.run(
            operation,
            operation_name="test.quota_guard",
            dependency="provider",
            idempotent=True,
            before_attempt=before_attempt,
        )

    assert calls == 2
    assert usages[0].attempts == 2
    assert usages[0].retries == 1
    assert usages[0].reason == "before_attempt_failed"


@pytest.mark.parametrize(
    ("retry_after", "now", "expected_wait"),
    [
        ("4", datetime(2026, 7, 12, tzinfo=UTC), 4.0),
        ("Sun, 12 Jul 2026 00:00:05 GMT", datetime(2026, 7, 12, tzinfo=UTC), 5.0),
        ("not-a-date", datetime(2026, 7, 12, tzinfo=UTC), 1.0),
    ],
)
def test_retry_executor_respects_valid_retry_after_and_ignores_invalid_value(
    retry_after,
    now,
    expected_wait,
):
    calls = 0
    sleeps = []
    executor = RetryExecutor(
        RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=1,
            max_delay_seconds=1,
            jitter_ratio=0,
            max_retry_after_seconds=10,
        ),
        sleeper=sleeps.append,
        random_value=lambda: 0.5,
        now=lambda: now,
        event_logger=lambda event, **fields: None,
    )

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderRateLimit(retry_after)
        return "ok"

    assert executor.run(
        operation,
        operation_name="test.rate_limit",
        dependency="provider",
        idempotent=True,
    ) == "ok"
    assert sleeps == [expected_wait]


def test_retry_executor_refuses_unbounded_retry_after_wait():
    calls = 0
    sleeps = []
    events = []
    executor = _recording_executor(
        RetryPolicy(max_attempts=3, max_retry_after_seconds=10),
        sleeps=sleeps,
        events=events,
    )

    def operation():
        nonlocal calls
        calls += 1
        raise ProviderRateLimit("60")

    with pytest.raises(RateLimited):
        executor.run(
            operation,
            operation_name="test.rate_limit",
            dependency="provider",
            idempotent=True,
        )

    assert calls == 1
    assert sleeps == []
    assert events[-1][1]["reason"] == "retry_after_exceeds_limit"


def test_retry_executor_async_path_uses_same_policy():
    calls = 0
    async_sleeps = []
    usages = []
    executor = _recording_executor(
        RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=0.5,
            max_delay_seconds=1,
            jitter_ratio=0,
        ),
        async_sleeps=async_sleeps,
        usages=usages,
    )

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("private endpoint")
        return "async recovered"

    result = asyncio.run(
        executor.arun(
            operation,
            operation_name="test.async_read",
            dependency="provider",
            idempotent=True,
        )
    )

    assert result == "async recovered"
    assert calls == 2
    assert async_sleeps == [0.5]
    assert usages[0].outcome == "succeeded"


@pytest.mark.parametrize(
    "policy_kwargs",
    [
        {"max_attempts": 0},
        {"initial_delay_seconds": -1},
        {"max_delay_seconds": -1},
        {"backoff_multiplier": 0.5},
        {"jitter_ratio": 1.1},
        {"max_retry_after_seconds": -1},
    ],
)
def test_retry_policy_rejects_invalid_bounds(policy_kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**policy_kwargs)


def test_retry_executor_is_not_wired_around_tool_nodes_or_write_paths():
    source_root = Path(__file__).parents[1] / "tech_doc_agent"
    allowed = {
        Path("app/core/retry.py"),
        Path("app/services/assistants/assistant_base.py"),
        Path("app/services/assistants/model_factory.py"),
        Path("app/services/embedding.py"),
        Path("app/services/vectordb/web_search_backend.py"),
    }
    retry_aware_modules = {
        path.relative_to(source_root)
        for path in source_root.rglob("*.py")
        if "RetryExecutor" in path.read_text(encoding="utf-8")
        or "build_retry_executor" in path.read_text(encoding="utf-8")
    }

    assert retry_aware_modules == allowed
