import { SSE_EVENT_TYPES } from "../sseContract";
import type { SseEventType } from "../sseContract";
import { decodeSsePayload } from "./ssePayloads";
import type { SsePayload, SsePayloadMap } from "./ssePayloads";


export type { SsePayload, SsePayloadMap } from "./ssePayloads";

export type SseEnvelope = {
  [EventType in SseEventType]: {
    type: EventType;
    data: SsePayloadMap[EventType];
  };
}[SseEventType];

export type ParsedSseMessage =
  | { kind: "event"; envelope: SseEnvelope }
  | { kind: "unknown"; event: string; data: SsePayload }
  | { kind: "invalid"; event: SseEventType; data: SsePayload; error: string };


const KNOWN_EVENT_TYPES = new Set<string>(SSE_EVENT_TYPES);


export function parseSseMessage(event: string, rawData: string): ParsedSseMessage {
  const data = parseSseData(rawData);
  if (!KNOWN_EVENT_TYPES.has(event)) {
    return { kind: "unknown", event, data };
  }
  const eventType = event as SseEventType;
  try {
    return {
      kind: "event",
      envelope: {
        type: eventType,
        data: decodeSsePayload(eventType, data)
      } as SseEnvelope
    };
  } catch (error) {
    return {
      kind: "invalid",
      event: eventType,
      data,
      error: error instanceof Error ? error.message : "invalid SSE payload"
    };
  }
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
