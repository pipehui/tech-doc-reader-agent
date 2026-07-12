from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RecoveryMetrics:
    transport_recovered_operations: int
    transport_exhausted_operations: int
    transport_retries: int
    argument_repair_rounds: int
    reflection_terminations: int
    task_success: bool
    additional_attempts: int

    def to_payload(self) -> dict[str, int | bool]:
        return asdict(self)


def summarize_recovery_events(
    events: Iterable[Mapping[str, Any]],
    *,
    task_success: bool,
) -> RecoveryMetrics:
    transport_recovered_operations = 0
    transport_exhausted_operations = 0
    transport_retries = 0
    argument_repair_rounds = 0
    reflection_terminations = 0

    for event in events:
        event_name = event.get("event")
        if event_name == "retry.final":
            retries = _nonnegative_int(event.get("retries"))
            transport_retries += retries
            if event.get("outcome") == "succeeded" and retries > 0:
                transport_recovered_operations += 1
            elif event.get("outcome") == "exhausted":
                transport_exhausted_operations += 1
            continue

        if event_name == "reflection.started":
            argument_repair_rounds += 1
        elif event_name == "reflection.terminated":
            reflection_terminations += 1

    return RecoveryMetrics(
        transport_recovered_operations=transport_recovered_operations,
        transport_exhausted_operations=transport_exhausted_operations,
        transport_retries=transport_retries,
        argument_repair_rounds=argument_repair_rounds,
        reflection_terminations=reflection_terminations,
        task_success=task_success,
        additional_attempts=transport_retries + argument_repair_rounds,
    )


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


__all__ = ["RecoveryMetrics", "summarize_recovery_events"]
