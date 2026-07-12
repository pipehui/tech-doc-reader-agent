export const SSE_EVENT_TYPES = [
  "token",
  "session_snapshot",
  "agent_message",
  "agent_transition",
  "plan_update",
  "structured_result",
  "usage_update",
  "budget_started",
  "budget_terminated",
  "context_metrics_update",
  "tool_call",
  "tool_result",
  "guardrail_blocked",
  "interrupt_required",
  "no_pending_interrupt",
  "done",
  "error"
] as const;

export type SseEventType = (typeof SSE_EVENT_TYPES)[number];

export const TOOL_RESULT_STATUSES = ["success", "error"] as const;
export type ToolResultStatus = (typeof TOOL_RESULT_STATUSES)[number];

export const ERROR_DETAIL_FIELDS = [
  "code",
  "retryable",
  "safe_message",
  "dependency",
  "cause_type"
] as const;

export const INSPECTOR_EVENT_TYPES = SSE_EVENT_TYPES.filter((event) => event !== "token");
