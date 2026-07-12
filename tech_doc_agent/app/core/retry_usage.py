from __future__ import annotations

import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from tech_doc_agent.app.core.errors import ValidationError


RetryOutcome = Literal["succeeded", "failed", "exhausted"]


class RetryUsageObserver(Protocol):
    def __call__(self, usage: RetryUsage) -> None: ...


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

    def __post_init__(self) -> None:
        for field_name in ("operation", "dependency", "reason"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{field_name} must be a non-empty trimmed string")
        for field_name in ("tool", "error_code"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str) or not value or value != value.strip()
            ):
                raise ValueError(
                    f"{field_name} must be None or a non-empty trimmed string"
                )
        if not isinstance(self.idempotent, bool):
            raise ValueError("idempotent must be a boolean")
        _validate_nonnegative_int(self.attempts, field="attempts")
        _validate_nonnegative_int(self.retries, field="retries")
        if self.retries > max(0, self.attempts - 1):
            raise ValueError("retries cannot exceed completed attempts minus one")
        if (
            isinstance(self.waited_seconds, bool)
            or not isinstance(self.waited_seconds, int | float)
            or not math.isfinite(float(self.waited_seconds))
            or self.waited_seconds < 0
        ):
            raise ValueError("waited_seconds must be a finite non-negative number")
        if self.outcome not in ("succeeded", "failed", "exhausted"):
            raise ValueError("outcome is invalid")
        if self.outcome == "succeeded" and self.attempts < 1:
            raise ValueError("succeeded retry usage must include a provider attempt")

    @classmethod
    def from_payload(cls, payload: Any) -> RetryUsage:
        if not isinstance(payload, dict):
            raise _invalid_retry_usage()
        try:
            return cls(
                operation=payload["operation"],
                dependency=payload["dependency"],
                tool=payload.get("tool"),
                idempotent=payload["idempotent"],
                attempts=payload["attempts"],
                retries=payload["retries"],
                waited_seconds=payload["waited_seconds"],
                outcome=payload["outcome"],
                reason=payload["reason"],
                error_code=payload.get("error_code"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _invalid_retry_usage(type(exc).__name__) from exc

    def to_payload(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "dependency": self.dependency,
            "tool": self.tool,
            "idempotent": self.idempotent,
            "attempts": self.attempts,
            "retries": self.retries,
            "waited_seconds": round(float(self.waited_seconds), 6),
            "outcome": self.outcome,
            "reason": self.reason,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class RetryUsageLedger:
    operations: tuple[RetryUsage, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("RetryUsageLedger only supports schema_version 1")
        if any(not isinstance(usage, RetryUsage) for usage in self.operations):
            raise ValueError("RetryUsageLedger operations must contain RetryUsage values")

    @classmethod
    def from_state(cls, payload: Any) -> RetryUsageLedger:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise _invalid_retry_ledger()
        raw_operations = payload.get("operations")
        if not isinstance(raw_operations, list):
            raise _invalid_retry_ledger("operations")
        ledger = cls(
            operations=tuple(
                RetryUsage.from_payload(operation)
                for operation in raw_operations
            )
        )
        if payload.get("summary") != ledger.summary_payload():
            raise _invalid_retry_ledger("summary")
        return ledger

    def record(self, usages: tuple[RetryUsage, ...]) -> RetryUsageLedger:
        if any(not isinstance(usage, RetryUsage) for usage in usages):
            raise ValueError("Recorded retry usages must contain RetryUsage values")
        return replace(self, operations=(*self.operations, *usages))

    def summary_payload(self) -> dict[str, Any]:
        dependencies: dict[str, dict[str, Any]] = {}
        for usage in self.operations:
            dependency = dependencies.setdefault(
                usage.dependency,
                {
                    "operations": 0,
                    "attempts": 0,
                    "retries": 0,
                    "waited_seconds": 0.0,
                },
            )
            dependency["operations"] += 1
            dependency["attempts"] += usage.attempts
            dependency["retries"] += usage.retries
            dependency["waited_seconds"] += usage.waited_seconds
        for dependency in dependencies.values():
            dependency["waited_seconds"] = round(
                float(dependency["waited_seconds"]),
                6,
            )
        return {
            "operations": len(self.operations),
            "attempts": sum(usage.attempts for usage in self.operations),
            "retries": sum(usage.retries for usage in self.operations),
            "waited_seconds": round(
                sum(usage.waited_seconds for usage in self.operations),
                6,
            ),
            "recovered_operations": sum(
                usage.outcome == "succeeded" and usage.retries > 0
                for usage in self.operations
            ),
            "exhausted_operations": sum(
                usage.outcome == "exhausted" for usage in self.operations
            ),
            "failed_operations": sum(
                usage.outcome == "failed" for usage in self.operations
            ),
            "dependencies": {
                name: dependencies[name]
                for name in sorted(dependencies)
            },
        }

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operations": [usage.to_payload() for usage in self.operations],
            "summary": self.summary_payload(),
        }


@dataclass(slots=True)
class RetryUsageCollector:
    _operations: list[RetryUsage] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, usage: RetryUsage) -> None:
        if not isinstance(usage, RetryUsage):
            raise TypeError("RetryUsageCollector accepts RetryUsage values")
        with self._lock:
            self._operations.append(usage)

    def snapshot(self) -> tuple[RetryUsage, ...]:
        with self._lock:
            return tuple(self._operations)


_CURRENT_RETRY_USAGE_COLLECTOR: ContextVar[RetryUsageCollector | None] = ContextVar(
    "retry_usage_collector",
    default=None,
)


@contextmanager
def capture_retry_usage() -> Iterator[RetryUsageCollector]:
    collector = RetryUsageCollector()
    token = _CURRENT_RETRY_USAGE_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _CURRENT_RETRY_USAGE_COLLECTOR.reset(token)


def observe_retry_usage(usage: RetryUsage) -> None:
    collector = _CURRENT_RETRY_USAGE_COLLECTOR.get()
    if collector is not None:
        collector.record(usage)


def retry_usage_delta_payload(
    usages: tuple[RetryUsage, ...],
    *,
    kind: Literal["reset", "operations"] = "operations",
) -> dict[str, Any]:
    delta = RetryUsageLedger().record(usages)
    return {
        "kind": kind,
        "operations": [usage.to_payload() for usage in usages],
        "summary": delta.summary_payload(),
    }


def _validate_nonnegative_int(value: Any, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


def _invalid_retry_usage(cause_type: str = "RetryUsageValidation") -> ValidationError:
    return ValidationError(
        "The provider retry usage payload is invalid.",
        code="retry_usage_invalid",
        dependency="workflow_state",
        cause_type=cause_type,
    )


def _invalid_retry_ledger(cause_type: str = "RetryLedgerValidation") -> ValidationError:
    return ValidationError(
        "The provider retry usage ledger is invalid.",
        code="retry_usage_ledger_invalid",
        dependency="workflow_state",
        cause_type=cause_type,
    )


__all__ = [
    "RetryOutcome",
    "RetryUsage",
    "RetryUsageCollector",
    "RetryUsageLedger",
    "RetryUsageObserver",
    "capture_retry_usage",
    "observe_retry_usage",
    "retry_usage_delta_payload",
]
