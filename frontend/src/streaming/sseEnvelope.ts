import { SSE_EVENT_TYPES } from "../sseContract";
import type { SseEventType } from "../sseContract";


export type SsePayload = Record<string, unknown>;

export interface SsePayloadMap {
  token: SsePayload & { text?: unknown; agent?: unknown };
  session_snapshot: SsePayload;
  agent_message: SsePayload & { agent?: unknown; node?: unknown; content?: unknown };
  agent_transition: SsePayload & { agent?: unknown; phase?: unknown };
  plan_update: SsePayload & { plan?: unknown; plan_index?: unknown; learning_target?: unknown };
  structured_result: SsePayload & { node?: unknown; result_key?: unknown; result?: unknown; parsed?: unknown };
  usage_update: SsePayload & { node?: unknown; delta?: unknown; usage?: unknown };
  budget_started: SsePayload & { node?: unknown; status?: unknown; usage?: unknown };
  budget_terminated: SsePayload & { node?: unknown; termination?: unknown; usage?: unknown };
  context_metrics_update: SsePayload & { node?: unknown; delta?: unknown; metrics?: unknown };
  tool_call: SsePayload & { agent?: unknown; node?: unknown; tool?: unknown; args?: unknown; tool_call_id?: unknown };
  tool_result: SsePayload & { agent?: unknown; node?: unknown; tool?: unknown; content?: unknown; tool_call_id?: unknown; status?: unknown; error?: unknown; safe_message?: unknown; code?: unknown; retryable?: unknown; dependency?: unknown; cause_type?: unknown };
  guardrail_blocked: SsePayload & { message?: unknown; findings?: unknown };
  interrupt_required: SsePayload & { pending?: unknown; session_id?: unknown };
  no_pending_interrupt: SsePayload & { session_id?: unknown };
  done: SsePayload & { session_id?: unknown };
  error: SsePayload & { status?: unknown; code?: unknown; retryable?: unknown; message?: unknown; safe_message?: unknown; dependency?: unknown; cause_type?: unknown; session_id?: unknown };
}

export type SseEnvelope = {
  [EventType in SseEventType]: {
    type: EventType;
    data: SsePayloadMap[EventType];
  };
}[SseEventType];

export type ParsedSseMessage =
  | { kind: "event"; envelope: SseEnvelope }
  | { kind: "unknown"; event: string; data: SsePayload };


const KNOWN_EVENT_TYPES = new Set<string>(SSE_EVENT_TYPES);


export function parseSseMessage(event: string, rawData: string): ParsedSseMessage {
  const data = parseSseData(rawData);
  if (!KNOWN_EVENT_TYPES.has(event)) {
    return { kind: "unknown", event, data };
  }
  return {
    kind: "event",
    envelope: {
      type: event as SseEventType,
      data
    } as SseEnvelope
  };
}


export function parseSseData(raw: string): SsePayload {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed === "string" && /^[\[{]/.test(parsed.trim())) {
      return asPayload(JSON.parse(parsed) as unknown);
    }
    return asPayload(parsed);
  } catch {
    return { raw, message: raw };
  }
}


function asPayload(value: unknown): SsePayload {
  return typeof value === "object" && value !== null
    ? value as SsePayload
    : { value };
}
