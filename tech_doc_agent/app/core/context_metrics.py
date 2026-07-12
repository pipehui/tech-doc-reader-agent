from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from tech_doc_agent.app.core.budget import LlmUsage
from tech_doc_agent.app.core.errors import ValidationError


ContextScope = Literal["full", "scoped"]


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    agent: str
    scope: ContextScope
    checkpoint_message_count: int
    checkpoint_serialized_bytes: int | None
    prompt_message_count: int
    prompt_serialized_bytes: int | None

    def __post_init__(self) -> None:
        _required_string(self.agent, "agent")
        if self.scope not in {"full", "scoped"}:
            raise ValueError("scope must be full or scoped")
        for field_name, count in (
            ("checkpoint_message_count", self.checkpoint_message_count),
            ("prompt_message_count", self.prompt_message_count),
        ):
            _nonnegative_int(count, field_name)
        for field_name, serialized_bytes in (
            ("checkpoint_serialized_bytes", self.checkpoint_serialized_bytes),
            ("prompt_serialized_bytes", self.prompt_serialized_bytes),
        ):
            _optional_nonnegative_int(serialized_bytes, field_name)


@dataclass(frozen=True, slots=True)
class AgentContextMetrics:
    invocations: int = 0
    llm_calls: int = 0
    scope: ContextScope = "full"
    last_checkpoint_message_count: int = 0
    max_checkpoint_message_count: int = 0
    last_checkpoint_serialized_bytes: int | None = 0
    max_checkpoint_serialized_bytes: int = 0
    last_prompt_message_count: int = 0
    max_prompt_message_count: int = 0
    last_prompt_serialized_bytes: int | None = 0
    max_prompt_serialized_bytes: int = 0
    reported_input_tokens: int = 0
    unreported_input_token_calls: int = 0
    last_input_tokens: int | None = 0
    serialization_unreported_invocations: int = 0

    @classmethod
    def from_state(cls, payload: Any) -> AgentContextMetrics:
        if not isinstance(payload, dict):
            raise _invalid_context_metrics()
        scope = payload.get("scope")
        if scope not in {"full", "scoped"}:
            raise _invalid_context_metrics("scope")
        integer_fields = {
            name: _state_nonnegative_int(payload.get(name), name)
            for name in (
                "invocations",
                "llm_calls",
                "last_checkpoint_message_count",
                "max_checkpoint_message_count",
                "max_checkpoint_serialized_bytes",
                "last_prompt_message_count",
                "max_prompt_message_count",
                "max_prompt_serialized_bytes",
                "reported_input_tokens",
                "unreported_input_token_calls",
                "serialization_unreported_invocations",
            )
        }
        return cls(
            scope=scope,
            last_checkpoint_serialized_bytes=_state_optional_nonnegative_int(
                payload.get("last_checkpoint_serialized_bytes"),
                "last_checkpoint_serialized_bytes",
            ),
            last_prompt_serialized_bytes=_state_optional_nonnegative_int(
                payload.get("last_prompt_serialized_bytes"),
                "last_prompt_serialized_bytes",
            ),
            last_input_tokens=_state_optional_nonnegative_int(
                payload.get("last_input_tokens"),
                "last_input_tokens",
            ),
            **integer_fields,
        )

    @property
    def input_tokens(self) -> int | None:
        return (
            None
            if self.unreported_input_token_calls
            else self.reported_input_tokens
        )

    def record(
        self,
        snapshot: ContextSnapshot,
        usages: tuple[LlmUsage, ...],
    ) -> AgentContextMetrics:
        llm_calls = sum(usage.calls for usage in usages)
        reported_input_tokens = sum(usage.input_tokens or 0 for usage in usages)
        unreported_input_calls = sum(
            usage.calls for usage in usages if usage.input_tokens is None
        )
        last_input_tokens = (
            None if unreported_input_calls else reported_input_tokens
        )
        checkpoint_bytes = snapshot.checkpoint_serialized_bytes
        prompt_bytes = snapshot.prompt_serialized_bytes
        serialization_unreported = int(
            checkpoint_bytes is None or prompt_bytes is None
        )
        return replace(
            self,
            invocations=self.invocations + 1,
            llm_calls=self.llm_calls + llm_calls,
            scope=snapshot.scope,
            last_checkpoint_message_count=snapshot.checkpoint_message_count,
            max_checkpoint_message_count=max(
                self.max_checkpoint_message_count,
                snapshot.checkpoint_message_count,
            ),
            last_checkpoint_serialized_bytes=checkpoint_bytes,
            max_checkpoint_serialized_bytes=max(
                self.max_checkpoint_serialized_bytes,
                checkpoint_bytes or 0,
            ),
            last_prompt_message_count=snapshot.prompt_message_count,
            max_prompt_message_count=max(
                self.max_prompt_message_count,
                snapshot.prompt_message_count,
            ),
            last_prompt_serialized_bytes=prompt_bytes,
            max_prompt_serialized_bytes=max(
                self.max_prompt_serialized_bytes,
                prompt_bytes or 0,
            ),
            reported_input_tokens=(
                self.reported_input_tokens + reported_input_tokens
            ),
            unreported_input_token_calls=(
                self.unreported_input_token_calls + unreported_input_calls
            ),
            last_input_tokens=last_input_tokens,
            serialization_unreported_invocations=(
                self.serialization_unreported_invocations
                + serialization_unreported
            ),
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "invocations": self.invocations,
            "llm_calls": self.llm_calls,
            "scope": self.scope,
            "last_checkpoint_message_count": self.last_checkpoint_message_count,
            "max_checkpoint_message_count": self.max_checkpoint_message_count,
            "last_checkpoint_serialized_bytes": self.last_checkpoint_serialized_bytes,
            "max_checkpoint_serialized_bytes": self.max_checkpoint_serialized_bytes,
            "last_prompt_message_count": self.last_prompt_message_count,
            "max_prompt_message_count": self.max_prompt_message_count,
            "last_prompt_serialized_bytes": self.last_prompt_serialized_bytes,
            "max_prompt_serialized_bytes": self.max_prompt_serialized_bytes,
            "input_tokens": self.input_tokens,
            "reported_input_tokens": self.reported_input_tokens,
            "unreported_input_token_calls": self.unreported_input_token_calls,
            "last_input_tokens": self.last_input_tokens,
            "serialization_unreported_invocations": (
                self.serialization_unreported_invocations
            ),
        }


@dataclass(frozen=True, slots=True)
class ContextMetrics:
    schema_version: int
    measurements: int
    agents: dict[str, AgentContextMetrics]

    @classmethod
    def new(cls) -> ContextMetrics:
        return cls(schema_version=1, measurements=0, agents={})

    @classmethod
    def from_state(cls, payload: Any) -> ContextMetrics:
        if not isinstance(payload, dict):
            raise _invalid_context_metrics()
        schema_version = payload.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 1
        ):
            raise _invalid_context_metrics("schema_version")
        measurements = _state_nonnegative_int(
            payload.get("measurements"),
            "measurements",
        )
        raw_agents = payload.get("agents")
        if not isinstance(raw_agents, dict):
            raise _invalid_context_metrics("agents")
        agents = {}
        for agent, value in raw_agents.items():
            if not isinstance(agent, str) or not agent or agent != agent.strip():
                raise _invalid_context_metrics("agent")
            agents[agent] = AgentContextMetrics.from_state(value)
        if measurements != sum(item.invocations for item in agents.values()):
            raise _invalid_context_metrics("measurements")
        return cls(schema_version=1, measurements=measurements, agents=agents)

    def record(
        self,
        snapshot: ContextSnapshot,
        usages: tuple[LlmUsage, ...],
    ) -> ContextMetrics:
        agents = dict(self.agents)
        agents[snapshot.agent] = agents.get(
            snapshot.agent,
            AgentContextMetrics(scope=snapshot.scope),
        ).record(snapshot, usages)
        return ContextMetrics(
            schema_version=1,
            measurements=self.measurements + 1,
            agents=agents,
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "measurements": self.measurements,
            "agents": {
                agent: metrics.to_state()
                for agent, metrics in sorted(self.agents.items())
            },
        }


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    return value


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _optional_nonnegative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field_name)


def _state_nonnegative_int(value: Any, field_name: str) -> int:
    try:
        return _nonnegative_int(value, field_name)
    except ValueError:
        raise _invalid_context_metrics(field_name) from None


def _state_optional_nonnegative_int(value: Any, field_name: str) -> int | None:
    try:
        return _optional_nonnegative_int(value, field_name)
    except ValueError:
        raise _invalid_context_metrics(field_name) from None


def _invalid_context_metrics(field_name: str | None = None) -> ValidationError:
    return ValidationError(
        "The persisted context metrics are invalid.",
        code="context_metrics_invalid",
        dependency="workflow_state",
        cause_type=field_name or "ContextMetricsValidation",
    )


__all__ = [
    "AgentContextMetrics",
    "ContextMetrics",
    "ContextScope",
    "ContextSnapshot",
]
