from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tech_doc_agent.app.core.budget import LlmUsage
from tech_doc_agent.app.core.context_metrics import (
    ContextMetrics,
    ContextScope,
    ContextSnapshot,
)
from tech_doc_agent.app.core.context_serialization import measure_context
from tech_doc_agent.app.core.errors import ValidationError
from tech_doc_agent.app.core.observability import log_event

from .state import State


@dataclass(slots=True)
class ContextMetricsTracker:
    event_logger: Callable[..., None] = log_event

    def current(self, state: State) -> ContextMetrics:
        payload = state.get("context_metrics")
        if payload is None:
            return ContextMetrics.new()
        return ContextMetrics.from_state(payload)

    def snapshot(
        self,
        state: State,
        prompt_state: Mapping[str, Any],
        *,
        agent: str,
        scope: ContextScope,
    ) -> ContextSnapshot:
        snapshot = measure_context(
            state=state,
            prompt_state=prompt_state,
            agent=agent,
            scope=scope,
        )
        self.event_logger(
            "context.input.measured",
            agent=snapshot.agent,
            scope=snapshot.scope,
            checkpoint_message_count=snapshot.checkpoint_message_count,
            checkpoint_serialized_bytes=snapshot.checkpoint_serialized_bytes,
            prompt_message_count=snapshot.prompt_message_count,
            prompt_serialized_bytes=snapshot.prompt_serialized_bytes,
        )
        return snapshot

    def record_assistant(
        self,
        state: State,
        assistant_update: dict[str, Any],
        snapshot: ContextSnapshot,
    ) -> dict[str, Any]:
        raw_usages = assistant_update.get("_llm_usage", ())
        if not isinstance(raw_usages, (list, tuple)) or any(
            not isinstance(usage, LlmUsage) for usage in raw_usages
        ):
            raise ValidationError(
                "The assistant returned invalid context usage metadata.",
                code="context_usage_invalid",
                dependency="llm",
                cause_type=type(raw_usages).__name__,
            )
        usages = tuple(raw_usages)
        metrics = self.current(state).record(snapshot, usages)
        llm_calls = sum(usage.calls for usage in usages)
        reported_input_tokens = sum(usage.input_tokens or 0 for usage in usages)
        unreported_input_calls = sum(
            usage.calls for usage in usages if usage.input_tokens is None
        )
        input_tokens = (
            None if unreported_input_calls else reported_input_tokens
        )
        self.event_logger(
            "context.input.completed",
            agent=snapshot.agent,
            scope=snapshot.scope,
            checkpoint_message_count=snapshot.checkpoint_message_count,
            checkpoint_serialized_bytes=snapshot.checkpoint_serialized_bytes,
            prompt_message_count=snapshot.prompt_message_count,
            prompt_serialized_bytes=snapshot.prompt_serialized_bytes,
            llm_calls_delta=llm_calls,
            input_tokens_delta=input_tokens,
            reported_input_tokens_delta=reported_input_tokens,
            unreported_input_token_calls_delta=unreported_input_calls,
        )
        return {
            **assistant_update,
            "context_metrics": metrics.to_state(),
            "context_metrics_delta": {
                "kind": "assistant",
                "agent": snapshot.agent,
                "scope": snapshot.scope,
                "checkpoint_message_count": snapshot.checkpoint_message_count,
                "checkpoint_serialized_bytes": (
                    snapshot.checkpoint_serialized_bytes
                ),
                "prompt_message_count": snapshot.prompt_message_count,
                "prompt_serialized_bytes": snapshot.prompt_serialized_bytes,
                "llm_calls": llm_calls,
                "input_tokens": input_tokens,
                "reported_input_tokens": reported_input_tokens,
                "unreported_input_token_calls": unreported_input_calls,
            },
        }


def context_metrics_request_start_node(
    node: Callable,
    tracker: ContextMetricsTracker,
) -> Callable:
    def invoke(state: State, config=None):
        metrics = ContextMetrics.new()
        update = node(state, config)
        return {
            **update,
            "context_metrics": metrics.to_state(),
            "context_metrics_delta": {"kind": "reset"},
        }

    return invoke


__all__ = [
    "ContextMetricsTracker",
    "context_metrics_request_start_node",
]
