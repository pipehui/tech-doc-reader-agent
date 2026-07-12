from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import math
import random
import time
from typing import Any, Literal, Protocol, TypeVar

from tech_doc_agent.app.core.errors import ApplicationError, classify_error
from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.settings import Settings


T = TypeVar("T")
RetryOutcome = Literal["succeeded", "failed", "exhausted"]


class EventLogger(Protocol):
    def __call__(self, event: str, **fields: Any) -> None: ...


class RetryUsageObserver(Protocol):
    def __call__(self, usage: RetryUsage) -> None: ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Finite transport retry policy for explicitly idempotent operations."""

    max_attempts: int = 3
    initial_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0
    backoff_multiplier: float = 2.0
    jitter_ratio: float = 0.2
    max_retry_after_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be greater than or equal to 1")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be greater than or equal to 0")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be greater than or equal to 0")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be greater than or equal to 1")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")
        if self.max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds must be greater than or equal to 0")


@dataclass(frozen=True, slots=True)
class RetryUsage:
    operation: str
    dependency: str
    tool: str | None
    idempotent: bool
    attempts: int
    retries: int
    waited_seconds: float
    outcome: RetryOutcome
    reason: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _RetryDecision:
    should_retry: bool
    outcome: RetryOutcome
    reason: str
    delay_seconds: float = 0.0
    retry_after_seconds: float | None = None


class RetryExecutor:
    """Execute one operation under an explicit, observable retry boundary."""

    def __init__(
        self,
        policy: RetryPolicy,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        async_sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        event_logger: EventLogger = log_event,
        usage_observer: RetryUsageObserver | None = None,
    ) -> None:
        self.policy = policy
        self._sleeper = sleeper
        self._async_sleeper = async_sleeper
        self._random_value = random_value
        self._now = now
        self._event_logger = event_logger
        self._usage_observer = usage_observer

    def run(
        self,
        operation: Callable[[], T],
        *,
        operation_name: str,
        dependency: str,
        idempotent: bool,
        tool: str | None = None,
        before_attempt: Callable[[int], None] | None = None,
    ) -> T:
        session = _RetrySession(
            executor=self,
            operation_name=operation_name,
            dependency=dependency,
            idempotent=idempotent,
            tool=tool,
            before_attempt=before_attempt,
        )

        for attempt in range(1, self.policy.max_attempts + 1):
            session.start_attempt(attempt)
            try:
                result = operation()
            except Exception as exc:
                delay_seconds = session.handle_failure(exc, attempt=attempt)
                self._sleeper(delay_seconds)
                session.record_wait(delay_seconds)
                continue

            session.finish_success(attempts=attempt)
            return result

        raise AssertionError("retry loop exited without a result or mapped error")

    async def arun(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        operation_name: str,
        dependency: str,
        idempotent: bool,
        tool: str | None = None,
        before_attempt: Callable[[int], None] | None = None,
    ) -> T:
        session = _RetrySession(
            executor=self,
            operation_name=operation_name,
            dependency=dependency,
            idempotent=idempotent,
            tool=tool,
            before_attempt=before_attempt,
        )

        for attempt in range(1, self.policy.max_attempts + 1):
            session.start_attempt(attempt)
            try:
                result = await operation()
            except Exception as exc:
                delay_seconds = session.handle_failure(exc, attempt=attempt)
                await self._async_sleeper(delay_seconds)
                session.record_wait(delay_seconds)
                continue

            session.finish_success(attempts=attempt)
            return result

        raise AssertionError("retry loop exited without a result or mapped error")

    def _decide(
        self,
        *,
        error: BaseException,
        mapped: ApplicationError,
        attempt: int,
        idempotent: bool,
    ) -> _RetryDecision:
        if not mapped.retryable:
            return _RetryDecision(False, "failed", "non_retryable")
        if not idempotent:
            return _RetryDecision(False, "failed", "non_idempotent")
        if attempt >= self.policy.max_attempts:
            return _RetryDecision(False, "exhausted", "max_attempts_exhausted")

        retry_after_seconds = _retry_after_seconds(error, now=self._now())
        if (
            retry_after_seconds is not None
            and retry_after_seconds > self.policy.max_retry_after_seconds
        ):
            return _RetryDecision(
                False,
                "failed",
                "retry_after_exceeds_limit",
                retry_after_seconds=retry_after_seconds,
            )

        delay_seconds = self._backoff_delay(attempt)
        if retry_after_seconds is not None:
            delay_seconds = max(delay_seconds, retry_after_seconds)
        return _RetryDecision(
            True,
            "failed",
            "retry_scheduled",
            delay_seconds=delay_seconds,
            retry_after_seconds=retry_after_seconds,
        )

    def _backoff_delay(self, failed_attempt: int) -> float:
        base_delay = min(
            self.policy.initial_delay_seconds
            * self.policy.backoff_multiplier ** (failed_attempt - 1),
            self.policy.max_delay_seconds,
        )
        jitter = base_delay * self.policy.jitter_ratio * (2 * self._random_value() - 1)
        return max(0.0, min(self.policy.max_delay_seconds, base_delay + jitter))

    def _log_scheduled(
        self,
        *,
        operation_name: str,
        dependency: str,
        tool: str | None,
        attempt: int,
        delay_seconds: float,
        retry_after_seconds: float | None,
        error: ApplicationError,
    ) -> None:
        self._event_logger(
            "retry.scheduled",
            operation=operation_name,
            dependency=dependency,
            tool=tool,
            attempt=attempt,
            next_attempt=attempt + 1,
            max_attempts=self.policy.max_attempts,
            wait_seconds=round(delay_seconds, 6),
            retry_after_seconds=(
                round(retry_after_seconds, 6) if retry_after_seconds is not None else None
            ),
            error_code=error.code,
            retryable=error.retryable,
            cause_type=error.cause_type,
        )

    def _finalize(
        self,
        *,
        operation_name: str,
        dependency: str,
        tool: str | None,
        idempotent: bool,
        attempts: int,
        retries: int,
        waited_seconds: float,
        outcome: RetryOutcome,
        reason: str,
        error: ApplicationError | None = None,
    ) -> None:
        usage = RetryUsage(
            operation=operation_name,
            dependency=dependency,
            tool=tool,
            idempotent=idempotent,
            attempts=attempts,
            retries=retries,
            waited_seconds=round(waited_seconds, 6),
            outcome=outcome,
            reason=reason,
            error_code=error.code if error is not None else None,
        )
        self._event_logger(
            "retry.final",
            operation=usage.operation,
            dependency=usage.dependency,
            tool=usage.tool,
            idempotent=usage.idempotent,
            attempts=usage.attempts,
            retries=usage.retries,
            waited_seconds=usage.waited_seconds,
            outcome=usage.outcome,
            reason=usage.reason,
            error_code=usage.error_code,
            cause_type=error.cause_type if error is not None else None,
        )
        if self._usage_observer is not None:
            self._usage_observer(usage)


@dataclass(slots=True)
class _RetrySession:
    executor: RetryExecutor
    operation_name: str
    dependency: str
    idempotent: bool
    tool: str | None = None
    before_attempt: Callable[[int], None] | None = None
    waited_seconds: float = 0.0

    def start_attempt(self, attempt: int) -> None:
        if self.before_attempt is not None:
            try:
                self.before_attempt(attempt)
            except Exception as exc:
                mapped = classify_error(exc, dependency=self.dependency, tool=self.tool)
                self._finalize(
                    attempts=attempt - 1,
                    outcome="failed",
                    reason="before_attempt_failed",
                    error=mapped,
                )
                raise mapped from exc

        self.executor._event_logger(
            "retry.attempt",
            operation=self.operation_name,
            dependency=self.dependency,
            tool=self.tool,
            attempt=attempt,
            max_attempts=self.executor.policy.max_attempts,
            idempotent=self.idempotent,
        )

    def handle_failure(self, error: BaseException, *, attempt: int) -> float:
        mapped = classify_error(error, dependency=self.dependency, tool=self.tool)
        decision = self.executor._decide(
            error=error,
            mapped=mapped,
            attempt=attempt,
            idempotent=self.idempotent,
        )
        if not decision.should_retry:
            self._finalize(
                attempts=attempt,
                outcome=decision.outcome,
                reason=decision.reason,
                error=mapped,
            )
            raise mapped from error

        self.executor._log_scheduled(
            operation_name=self.operation_name,
            dependency=self.dependency,
            tool=self.tool,
            attempt=attempt,
            delay_seconds=decision.delay_seconds,
            retry_after_seconds=decision.retry_after_seconds,
            error=mapped,
        )
        return decision.delay_seconds

    def record_wait(self, delay_seconds: float) -> None:
        self.waited_seconds += delay_seconds

    def finish_success(self, *, attempts: int) -> None:
        self._finalize(
            attempts=attempts,
            outcome="succeeded",
            reason="completed",
        )

    def _finalize(
        self,
        *,
        attempts: int,
        outcome: RetryOutcome,
        reason: str,
        error: ApplicationError | None = None,
    ) -> None:
        self.executor._finalize(
            operation_name=self.operation_name,
            dependency=self.dependency,
            tool=self.tool,
            idempotent=self.idempotent,
            attempts=attempts,
            retries=max(0, attempts - 1),
            waited_seconds=self.waited_seconds,
            outcome=outcome,
            reason=reason,
            error=error,
        )


def build_retry_executor(
    settings: Settings,
    *,
    usage_observer: RetryUsageObserver | None = None,
) -> RetryExecutor:
    return RetryExecutor(
        RetryPolicy(
            max_attempts=settings.TRANSPORT_RETRY_MAX_ATTEMPTS,
            initial_delay_seconds=settings.TRANSPORT_RETRY_INITIAL_DELAY_SECONDS,
            max_delay_seconds=settings.TRANSPORT_RETRY_MAX_DELAY_SECONDS,
            backoff_multiplier=settings.TRANSPORT_RETRY_BACKOFF_MULTIPLIER,
            jitter_ratio=settings.TRANSPORT_RETRY_JITTER_RATIO,
            max_retry_after_seconds=settings.TRANSPORT_RETRY_MAX_RETRY_AFTER_SECONDS,
        ),
        usage_observer=usage_observer,
    )


def _retry_after_seconds(error: BaseException, *, now: datetime) -> float | None:
    value = _retry_after_header(error)
    if value is None:
        return None

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        seconds = (retry_at - now).total_seconds()

    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _retry_after_header(error: BaseException) -> Any | None:
    for owner in (error, getattr(error, "response", None)):
        headers = getattr(owner, "headers", None)
        if headers is None:
            continue
        get_header = getattr(headers, "get", None)
        if callable(get_header):
            value = get_header("retry-after")
            if value is None:
                value = get_header("Retry-After")
            if value is not None:
                return value
        if isinstance(headers, Mapping):
            for key, value in headers.items():
                if str(key).casefold() == "retry-after":
                    return value
    return None


__all__ = [
    "RetryExecutor",
    "RetryPolicy",
    "RetryUsage",
    "RetryUsageObserver",
    "build_retry_executor",
]
