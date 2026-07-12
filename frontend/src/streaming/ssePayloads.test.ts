import { describe, expect, it } from "vitest";
import SSE_EXAMPLES from "../../../contracts/sse_v1_examples.json";
import { SSE_EVENT_TYPES } from "../sseContract";
import type { SseEventType } from "../sseContract";
import {
  decodeSsePayload,
  SsePayloadValidationError
} from "./ssePayloads";
import type { SsePayload } from "./ssePayloads";


const EXAMPLES = SSE_EXAMPLES as Record<SseEventType, SsePayload>;


describe("SSE payload runtime contract", () => {
  it("decodes the shared backend/frontend example for every event", () => {
    for (const event of SSE_EVENT_TYPES) {
      expect(() => decodeSsePayload(event, EXAMPLES[event])).not.toThrow();
    }
  });

  it("normalizes nullable session state fields to the store shape", () => {
    expect(decodeSsePayload("session_snapshot", EXAMPLES.session_snapshot)).toEqual({
      ...EXAMPLES.session_snapshot,
      budget_usage: undefined,
      budget_status: undefined,
      budget_termination: undefined,
      context_metrics: undefined,
      provider_retry_usage: undefined
    });
  });

  it("allows additive fields but validates shared trace context", () => {
    expect(decodeSsePayload("token", {
      ...EXAMPLES.token,
      trace_id: "trace-1",
      future_field: { enabled: true }
    })).toMatchObject({
      text: "hello",
      trace_id: "trace-1",
      future_field: { enabled: true }
    });

    expect(() => decodeSsePayload("token", {
      ...EXAMPLES.token,
      trace_id: 42
    })).toThrow("token.trace_id must be a string");
  });

  it.each([
    ["token", { agent: "primary" }, "token.text"],
    ["agent_transition", { agent: "parser", phase: "start" }, "agent_transition.phase"],
    ["plan_update", {}, "plan_update.payload"],
    ["tool_result", { ...EXAMPLES.tool_result, status: "pending" }, "tool_result.status"],
    ["done", {}, "done.session_id"]
  ] as const)("rejects malformed %s payloads", (event, payload, field) => {
    expect(() => decodeSsePayload(event, payload)).toThrow(field);
  });

  it("normalizes omitted optional interrupt metadata to null", () => {
    expect(decodeSsePayload("interrupt_required", {
      session_id: "session-1",
      pending: true
    })).toEqual({
      session_id: "session-1",
      pending: true,
      approval_kind: null,
      source: null,
      risk_level: null,
      findings: null
    });
  });

  it("raises a typed validation error with event and field", () => {
    try {
      decodeSsePayload("token", {});
      throw new Error("expected validation error");
    } catch (error) {
      expect(error).toBeInstanceOf(SsePayloadValidationError);
      expect(error).toMatchObject({ event: "token", field: "text" });
    }
  });
});
