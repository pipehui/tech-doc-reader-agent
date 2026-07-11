export const SSE_EVENT_TYPES = [
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
  "error"
] as const;

export type SseEventType = (typeof SSE_EVENT_TYPES)[number];

export const INSPECTOR_EVENT_TYPES = SSE_EVENT_TYPES.filter((event) => event !== "token");
