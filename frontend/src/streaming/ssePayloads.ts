import type { SessionState } from "../types";
import type { SseEventType, ToolResultStatus } from "../sseContract";
import { decodeSessionState } from "../shared/api/contracts";


export type SsePayload = Record<string, unknown>;

interface SseContextFields {
  trace_id?: string;
  session_id?: string;
  user_id?: string | null;
  namespace?: string | null;
}

type Payload<Fields extends object> = SsePayload & SseContextFields & Fields;
type RiskLevel = "none" | "low" | "medium" | "high";


export interface SsePayloadMap {
  token: Payload<{ text: string; agent: string | null }>;
  session_snapshot: Payload<SessionState>;
  agent_message: Payload<{
    agent: string;
    node: string;
    message_id: string | null;
    content: string;
  }>;
  agent_transition: Payload<{
    agent: string;
    phase: "enter" | "finish" | "leave";
  }>;
  plan_update: Payload<{
    plan?: string[];
    plan_index?: number;
    learning_target?: string | null;
  }>;
  structured_result: Payload<{
    node: string;
    result_key: "parser_result" | "relation_result";
    result: Record<string, unknown>;
    parsed: boolean;
  }>;
  usage_update: Payload<{
    node: string;
    delta: Record<string, unknown>;
    usage: Record<string, unknown>;
  }>;
  budget_started: Payload<{
    node: string;
    status: "active";
    usage: Record<string, unknown>;
  }>;
  budget_terminated: Payload<{
    node: string;
    termination: Record<string, unknown>;
    usage: Record<string, unknown> | null;
  }>;
  context_metrics_update: Payload<{
    node: string;
    delta: Record<string, unknown>;
    metrics: Record<string, unknown>;
  }>;
  tool_call: Payload<{
    agent: string;
    node: string;
    tool: string | null;
    args: Record<string, unknown>;
    tool_call_id: string | null;
  }>;
  tool_result: Payload<{
    agent: string;
    node: string;
    tool: string | null;
    content: string;
    tool_call_id: string | null;
    status: ToolResultStatus;
    error: string | null;
    safe_message: string | null;
    code: string | null;
    retryable: boolean | null;
    dependency: string | null;
    cause_type: string | null;
  }>;
  guardrail_blocked: Payload<{
    session_id: string;
    source: string;
    risk_level: RiskLevel;
    findings: string[];
  }>;
  interrupt_required: Payload<{
    session_id: string;
    pending: true;
    approval_kind: "guardrail_input" | null;
    source: string | null;
    risk_level: RiskLevel | null;
    findings: string[] | null;
  }>;
  no_pending_interrupt: Payload<{ session_id: string }>;
  done: Payload<{ session_id: string }>;
  error: Payload<{
    session_id: string;
    status: "error";
    code: string;
    retryable: boolean;
    message: string;
    safe_message: string;
    dependency: string | null;
    cause_type: string;
  }>;
}


export class SsePayloadValidationError extends Error {
  readonly event: SseEventType;
  readonly field: string;

  constructor(event: SseEventType, field: string, expectation: string) {
    super(`${event}.${field} ${expectation}`);
    this.name = "SsePayloadValidationError";
    this.event = event;
    this.field = field;
  }
}


const DECODERS: {
  [EventType in SseEventType]: (data: SsePayload) => SsePayloadMap[EventType];
} = {
  token: (data) => withData(data, {
    text: requiredString("token", data, "text"),
    agent: nullableString("token", data, "agent")
  }),
  session_snapshot: decodeSessionSnapshot,
  agent_message: (data) => withData(data, {
    agent: requiredString("agent_message", data, "agent"),
    node: requiredString("agent_message", data, "node"),
    message_id: nullableString("agent_message", data, "message_id"),
    content: requiredString("agent_message", data, "content")
  }),
  agent_transition: (data) => withData(data, {
    agent: requiredString("agent_transition", data, "agent"),
    phase: requiredLiteral(
      "agent_transition",
      data,
      "phase",
      ["enter", "finish", "leave"] as const
    )
  }),
  plan_update: decodePlanUpdate,
  structured_result: (data) => withData(data, {
    node: requiredString("structured_result", data, "node"),
    result_key: requiredLiteral(
      "structured_result",
      data,
      "result_key",
      ["parser_result", "relation_result"] as const
    ),
    result: requiredObject("structured_result", data, "result"),
    parsed: requiredBoolean("structured_result", data, "parsed")
  }),
  usage_update: (data) => withData(data, {
    node: requiredString("usage_update", data, "node"),
    delta: requiredObject("usage_update", data, "delta"),
    usage: requiredObject("usage_update", data, "usage")
  }),
  budget_started: (data) => withData(data, {
    node: requiredString("budget_started", data, "node"),
    status: requiredLiteral("budget_started", data, "status", ["active"] as const),
    usage: requiredObject("budget_started", data, "usage")
  }),
  budget_terminated: (data) => withData(data, {
    node: requiredString("budget_terminated", data, "node"),
    termination: requiredObject("budget_terminated", data, "termination"),
    usage: nullableObject("budget_terminated", data, "usage")
  }),
  context_metrics_update: (data) => withData(data, {
    node: requiredString("context_metrics_update", data, "node"),
    delta: requiredObject("context_metrics_update", data, "delta"),
    metrics: requiredObject("context_metrics_update", data, "metrics")
  }),
  tool_call: (data) => withData(data, {
    agent: requiredString("tool_call", data, "agent"),
    node: requiredString("tool_call", data, "node"),
    tool: nullableString("tool_call", data, "tool"),
    args: requiredObject("tool_call", data, "args"),
    tool_call_id: nullableString("tool_call", data, "tool_call_id")
  }),
  tool_result: (data) => withData(data, {
    agent: requiredString("tool_result", data, "agent"),
    node: requiredString("tool_result", data, "node"),
    tool: nullableString("tool_result", data, "tool"),
    content: requiredString("tool_result", data, "content"),
    tool_call_id: nullableString("tool_result", data, "tool_call_id"),
    status: requiredLiteral(
      "tool_result",
      data,
      "status",
      ["success", "error"] as const
    ),
    error: nullableString("tool_result", data, "error"),
    safe_message: nullableString("tool_result", data, "safe_message"),
    code: nullableString("tool_result", data, "code"),
    retryable: nullableBoolean("tool_result", data, "retryable"),
    dependency: nullableString("tool_result", data, "dependency"),
    cause_type: nullableString("tool_result", data, "cause_type")
  }),
  guardrail_blocked: (data) => withData(data, {
    session_id: requiredString("guardrail_blocked", data, "session_id"),
    source: requiredString("guardrail_blocked", data, "source"),
    risk_level: requiredLiteral(
      "guardrail_blocked",
      data,
      "risk_level",
      ["none", "low", "medium", "high"] as const
    ),
    findings: requiredStringArray("guardrail_blocked", data, "findings")
  }),
  interrupt_required: (data) => withData(data, {
    session_id: requiredString("interrupt_required", data, "session_id"),
    pending: requiredLiteral("interrupt_required", data, "pending", [true] as const),
    approval_kind: nullableLiteral(
      "interrupt_required",
      data,
      "approval_kind",
      ["guardrail_input"] as const
    ),
    source: nullableString("interrupt_required", data, "source"),
    risk_level: nullableLiteral(
      "interrupt_required",
      data,
      "risk_level",
      ["none", "low", "medium", "high"] as const
    ),
    findings: nullableStringArray("interrupt_required", data, "findings")
  }),
  no_pending_interrupt: (data) => withData(data, {
    session_id: requiredString("no_pending_interrupt", data, "session_id")
  }),
  done: (data) => withData(data, {
    session_id: requiredString("done", data, "session_id")
  }),
  error: (data) => withData(data, {
    session_id: requiredString("error", data, "session_id"),
    status: requiredLiteral("error", data, "status", ["error"] as const),
    code: requiredString("error", data, "code"),
    retryable: requiredBoolean("error", data, "retryable"),
    message: requiredString("error", data, "message"),
    safe_message: requiredString("error", data, "safe_message"),
    dependency: nullableString("error", data, "dependency"),
    cause_type: requiredString("error", data, "cause_type")
  })
};


export function decodeSsePayload<EventType extends SseEventType>(
  event: EventType,
  data: SsePayload
): SsePayloadMap[EventType] {
  validateContext(event, data);
  return DECODERS[event](data);
}


function decodeSessionSnapshot(data: SsePayload): SsePayloadMap["session_snapshot"] {
  const state = decodeSessionState(data);
  return withData(data, {
    ...state,
    budget_usage: state.budget_usage,
    budget_status: state.budget_status,
    budget_termination: state.budget_termination,
    context_metrics: state.context_metrics
  });
}


function decodePlanUpdate(data: SsePayload): SsePayloadMap["plan_update"] {
  const event = "plan_update";
  const update: {
    plan?: string[];
    plan_index?: number;
    learning_target?: string | null;
  } = {};
  if (has(data, "plan")) update.plan = requiredStringArray(event, data, "plan");
  if (has(data, "plan_index")) {
    update.plan_index = requiredNonNegativeInteger(event, data, "plan_index");
  }
  if (has(data, "learning_target")) {
    update.learning_target = nullableString(event, data, "learning_target");
  }
  if (Object.keys(update).length === 0) {
    throw new SsePayloadValidationError(event, "payload", "must contain an update field");
  }
  return withData(data, update);
}


function validateContext(event: SseEventType, data: SsePayload) {
  for (const field of ["trace_id", "session_id"] as const) {
    if (has(data, field) && typeof data[field] !== "string") {
      throw new SsePayloadValidationError(event, field, "must be a string");
    }
  }
  for (const field of ["user_id", "namespace"] as const) {
    if (has(data, field) && data[field] !== null && typeof data[field] !== "string") {
      throw new SsePayloadValidationError(event, field, "must be a string or null");
    }
  }
}


function withData<Fields extends object>(data: SsePayload, fields: Fields): Payload<Fields> {
  return { ...data, ...fields } as Payload<Fields>;
}


function requiredString(event: SseEventType, data: SsePayload, field: string) {
  const value = data[field];
  if (typeof value !== "string") {
    throw new SsePayloadValidationError(event, field, "must be a string");
  }
  return value;
}


function nullableString(event: SseEventType, data: SsePayload, field: string) {
  const value = data[field];
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") {
    throw new SsePayloadValidationError(event, field, "must be a string or null");
  }
  return value;
}


function requiredBoolean(event: SseEventType, data: SsePayload, field: string) {
  const value = data[field];
  if (typeof value !== "boolean") {
    throw new SsePayloadValidationError(event, field, "must be a boolean");
  }
  return value;
}


function nullableBoolean(event: SseEventType, data: SsePayload, field: string) {
  const value = data[field];
  if (value === undefined || value === null) return null;
  if (typeof value !== "boolean") {
    throw new SsePayloadValidationError(event, field, "must be a boolean or null");
  }
  return value;
}


function requiredNonNegativeInteger(
  event: SseEventType,
  data: SsePayload,
  field: string
) {
  const value = data[field];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new SsePayloadValidationError(event, field, "must be a non-negative integer");
  }
  return value;
}


function requiredObject(event: SseEventType, data: SsePayload, field: string) {
  const value = data[field];
  if (!isObject(value)) {
    throw new SsePayloadValidationError(event, field, "must be an object");
  }
  return value;
}


function nullableObject(event: SseEventType, data: SsePayload, field: string) {
  const value = data[field];
  if (value === undefined || value === null) return null;
  if (!isObject(value)) {
    throw new SsePayloadValidationError(event, field, "must be an object or null");
  }
  return value;
}


function requiredStringArray(event: SseEventType, data: SsePayload, field: string) {
  const value = data[field];
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new SsePayloadValidationError(event, field, "must be an array of strings");
  }
  return value as string[];
}


function nullableStringArray(event: SseEventType, data: SsePayload, field: string) {
  const value = data[field];
  if (value === undefined || value === null) return null;
  return requiredStringArray(event, data, field);
}


function requiredLiteral<const Values extends readonly unknown[]>(
  event: SseEventType,
  data: SsePayload,
  field: string,
  values: Values
): Values[number] {
  const value = data[field];
  if (!values.includes(value)) {
    throw new SsePayloadValidationError(event, field, `must be one of ${values.join(", ")}`);
  }
  return value as Values[number];
}


function nullableLiteral<const Values extends readonly unknown[]>(
  event: SseEventType,
  data: SsePayload,
  field: string,
  values: Values
): Values[number] | null {
  const value = data[field];
  if (value === undefined || value === null) return null;
  return requiredLiteral(event, data, field, values);
}


function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


function has(data: SsePayload, field: string) {
  return Object.prototype.hasOwnProperty.call(data, field);
}
