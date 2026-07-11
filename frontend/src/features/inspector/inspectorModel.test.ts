import { describe, expect, it } from "vitest";
import type { TraceEvent } from "../../types";
import {
  eventSummary,
  filteredEvents,
  getTimelineBounds,
  laneMarkerClass,
  positionForTime
} from "./inspectorModel";
import { buildTraceExport, traceExportFilename } from "./traceExport";


function event(
  type: string,
  timestamp: string,
  data: Record<string, unknown> = {}
): TraceEvent {
  return {
    id: `${type}-${timestamp}`,
    seq: 1,
    type,
    data,
    agent: "primary",
    responseId: null,
    timestamp
  };
}


describe("inspector model", () => {
  it("filters token and inactive event types without mutating input", () => {
    const events = [
      event("token", "2026-07-12T00:00:00.000Z"),
      event("tool_call", "2026-07-12T00:00:01.000Z"),
      event("done", "2026-07-12T00:00:02.000Z")
    ];

    expect(filteredEvents(events, new Set(["token", "tool_call"])))
      .toEqual([events[1]]);
    expect(events).toHaveLength(3);
  });

  it("includes stream start in bounds and keeps a non-zero time range", () => {
    const events = [event("agent_message", "2026-07-12T00:00:10.000Z", {
      meta: { stream_started_at: "2026-07-12T00:00:05.000Z" }
    })];

    expect(getTimelineBounds(events)).toEqual({
      min: Date.parse("2026-07-12T00:00:05.000Z"),
      max: Date.parse("2026-07-12T00:00:10.000Z")
    });
    expect(getTimelineBounds([], () => 100)).toEqual({ min: 100, max: 101 });
    expect(getTimelineBounds([
      event("done", "2026-07-12T00:00:00.000Z")
    ])).toEqual({
      min: Date.parse("2026-07-12T00:00:00.000Z"),
      max: Date.parse("2026-07-12T00:00:00.000Z") + 1
    });
  });

  it("positions timestamps inside a clamped percentage range", () => {
    const bounds = { min: 100, max: 200 };
    expect(positionForTime(new Date(150).toISOString(), bounds)).toBe(50);
    expect(positionForTime(new Date(0).toISOString(), bounds)).toBe(0);
    expect(positionForTime(new Date(300).toISOString(), bounds)).toBe(100);
    expect(positionForTime("invalid", bounds)).toBe(0);
  });

  it("summarizes and classifies inspector-specific event variants", () => {
    expect(eventSummary(event("tool_call", "2026-07-12", { tool: "search" })))
      .toBe("search call");
    expect(eventSummary(event("agent_transition", "2026-07-12", {
      phase: "enter",
      agent: "parser"
    }))).toBe("enter parser");
    expect(laneMarkerClass(event("agent_transition", "2026-07-12", {
      phase: "leave"
    }))).toBe("agent_transition transition-leave");
    expect(laneMarkerClass(event("tool_result", "2026-07-12", {
      content: "Traceback: failed"
    }))).toBe("tool_result tool_result_error");
  });

  it("builds a tenant-scoped deterministic trace export", () => {
    const events = [event("done", "2026-07-12T00:00:00.000Z")];
    const tenant = { user_id: "user-a", namespace: "docs" };

    expect(buildTraceExport(
      "session-1",
      tenant,
      events,
      () => new Date("2026-07-12T01:02:03.000Z")
    )).toEqual({
      session_id: "session-1",
      user_id: "user-a",
      namespace: "docs",
      events,
      exportedAt: "2026-07-12T01:02:03.000Z"
    });
    expect(traceExportFilename("session-1", tenant, 42))
      .toBe("trace_user-a_docs_session-1_42.json");
  });
});
