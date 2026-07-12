from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tech_doc_agent.app.core.guardrails import RiskLevel

from .contract import SSE_EVENT_NAMES, SseEventName, ToolResultStatus


class SsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    namespace: str | None = None


class TokenPayload(SsePayload):
    text: str
    agent: str | None = None


class SessionSnapshotPayload(SsePayload):
    session_id: str
    user_id: str | None
    namespace: str | None
    exists: bool
    pending_interrupt: bool
    learning_target: str | None
    message_count: int = Field(ge=0)
    current_agent: str | None
    workflow_plan: list[str]
    plan_index: int = Field(ge=0)
    budget_usage: dict[str, Any] | None = None
    budget_status: Literal["active", "terminating", "terminated"] | None = None
    budget_termination: dict[str, Any] | None = None
    context_metrics: dict[str, Any] | None = None


class AgentMessagePayload(SsePayload):
    agent: str
    node: str
    message_id: str | None = None
    content: str


class AgentTransitionPayload(SsePayload):
    agent: str
    phase: Literal["enter", "finish", "leave"]


class PlanUpdatePayload(SsePayload):
    plan: list[str] | None = None
    plan_index: int | None = Field(default=None, ge=0)
    learning_target: str | None = None

    @model_validator(mode="after")
    def require_update(self) -> PlanUpdatePayload:
        if not self.model_fields_set.intersection(
            {"plan", "plan_index", "learning_target"}
        ):
            raise ValueError("plan_update must contain at least one update field")
        return self


class StructuredResultPayload(SsePayload):
    node: str
    result_key: Literal["parser_result", "relation_result"]
    result: dict[str, Any]
    parsed: bool


class UsageUpdatePayload(SsePayload):
    node: str
    delta: dict[str, Any]
    usage: dict[str, Any]


class BudgetStartedPayload(SsePayload):
    node: str
    status: Literal["active"]
    usage: dict[str, Any]


class BudgetTerminatedPayload(SsePayload):
    node: str
    termination: dict[str, Any]
    usage: dict[str, Any] | None = None


class ContextMetricsUpdatePayload(SsePayload):
    node: str
    delta: dict[str, Any]
    metrics: dict[str, Any]


class ToolCallPayload(SsePayload):
    agent: str
    node: str
    tool: str | None = None
    args: dict[str, Any]
    tool_call_id: str | None = None


class ToolResultPayload(SsePayload):
    agent: str
    node: str
    tool: str | None = None
    tool_call_id: str | None = None
    content: str
    status: ToolResultStatus
    error: str | None = None
    safe_message: str | None = None
    code: str | None = None
    retryable: bool | None = None
    dependency: str | None = None
    cause_type: str | None = None


class GuardrailBlockedPayload(SsePayload):
    session_id: str
    source: str
    risk_level: RiskLevel
    findings: list[str]


class InterruptRequiredPayload(SsePayload):
    session_id: str
    pending: Literal[True]
    approval_kind: Literal["guardrail_input"] | None = None
    source: str | None = None
    risk_level: RiskLevel | None = None
    findings: list[str] | None = None


class SessionTerminalPayload(SsePayload):
    session_id: str


class ErrorPayload(SsePayload):
    session_id: str
    status: Literal["error"]
    code: str
    retryable: bool
    message: str
    safe_message: str
    dependency: str | None = None
    cause_type: str


SSE_PAYLOAD_MODELS: dict[SseEventName, type[SsePayload]] = {
    "token": TokenPayload,
    "session_snapshot": SessionSnapshotPayload,
    "agent_message": AgentMessagePayload,
    "agent_transition": AgentTransitionPayload,
    "plan_update": PlanUpdatePayload,
    "structured_result": StructuredResultPayload,
    "usage_update": UsageUpdatePayload,
    "budget_started": BudgetStartedPayload,
    "budget_terminated": BudgetTerminatedPayload,
    "context_metrics_update": ContextMetricsUpdatePayload,
    "tool_call": ToolCallPayload,
    "tool_result": ToolResultPayload,
    "guardrail_blocked": GuardrailBlockedPayload,
    "interrupt_required": InterruptRequiredPayload,
    "no_pending_interrupt": SessionTerminalPayload,
    "done": SessionTerminalPayload,
    "error": ErrorPayload,
}


def validate_sse_payload(
    event: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if event not in SSE_EVENT_NAMES:
        raise ValueError(f"Unsupported SSE event type: {event}")
    model_type = SSE_PAYLOAD_MODELS[cast(SseEventName, event)]
    model = model_type.model_validate(payload)
    return model.model_dump(mode="json", exclude_unset=True)


__all__ = [
    "SSE_PAYLOAD_MODELS",
    "SsePayload",
    "validate_sse_payload",
]
