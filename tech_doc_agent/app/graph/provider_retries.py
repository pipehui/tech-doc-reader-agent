from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tech_doc_agent.app.core.observability import log_event
from tech_doc_agent.app.core.retry_usage import (
    RetryUsage,
    RetryUsageLedger,
    retry_usage_delta_payload,
)

from .state import State


@dataclass(slots=True)
class ProviderRetryUsageTracker:
    event_logger: Callable[..., None] = log_event

    def current(self, state: State) -> RetryUsageLedger:
        payload = state.get("provider_retry_usage")
        if payload is None:
            return RetryUsageLedger()
        return RetryUsageLedger.from_state(payload)

    def record_tool_operations(
        self,
        state: State,
        tool_update: dict[str, Any],
        usages: tuple[RetryUsage, ...],
    ) -> dict[str, Any]:
        if not usages:
            return tool_update
        ledger = self.current(state).record(usages)
        delta = retry_usage_delta_payload(usages)
        summary = delta["summary"]
        cumulative = ledger.summary_payload()
        self.event_logger(
            "provider_retry.usage.recorded",
            operations_delta=summary["operations"],
            attempts_delta=summary["attempts"],
            retries_delta=summary["retries"],
            waited_seconds_delta=summary["waited_seconds"],
            recovered_operations_delta=summary["recovered_operations"],
            exhausted_operations_delta=summary["exhausted_operations"],
            operations=cumulative["operations"],
            attempts=cumulative["attempts"],
            retries=cumulative["retries"],
        )
        return {
            **tool_update,
            "provider_retry_usage": ledger.to_state(),
            "provider_retry_usage_delta": delta,
        }


def provider_retry_usage_request_start_node(
    node: Callable,
) -> Callable:
    def invoke(state: State, config=None):
        ledger = RetryUsageLedger()
        update = node(state, config)
        return {
            **update,
            "provider_retry_usage": ledger.to_state(),
            "provider_retry_usage_delta": retry_usage_delta_payload(
                (),
                kind="reset",
            ),
        }

    return invoke


__all__ = [
    "ProviderRetryUsageTracker",
    "provider_retry_usage_request_start_node",
]
