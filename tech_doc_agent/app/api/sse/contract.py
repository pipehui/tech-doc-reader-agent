from typing import Literal, get_args


SseEventName = Literal[
    "token",
    "session_snapshot",
    "agent_message",
    "agent_transition",
    "plan_update",
    "structured_result",
    "tool_call",
    "tool_result",
    "guardrail_blocked",
    "interrupt_required",
    "no_pending_interrupt",
    "done",
    "error",
]

SSE_EVENT_NAMES: frozenset[str] = frozenset(get_args(SseEventName))
