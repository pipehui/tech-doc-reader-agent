import { normalizeAgent } from "../../agentColors";
import type { TraceEvent } from "../../types";


export interface TimelineBounds {
  min: number;
  max: number;
}


export function filteredEvents(events: TraceEvent[], filters: Set<string>) {
  return events.filter((event) => (
    event.type !== "token" && filters.has(event.type)
  ));
}


export function getTimelineBounds(
  events: TraceEvent[],
  now: () => number = Date.now
): TimelineBounds {
  const times: number[] = [];
  events.forEach((event) => {
    const time = new Date(event.timestamp).getTime();
    if (Number.isFinite(time)) times.push(time);
    const meta = event.data.meta as { stream_started_at?: string } | undefined;
    const start = new Date(meta?.stream_started_at || "").getTime();
    if (Number.isFinite(start)) times.push(start);
  });
  if (!times.length) {
    const current = now();
    return { min: current, max: current + 1 };
  }
  const min = Math.min(...times);
  const max = Math.max(...times);
  return { min, max: max === min ? min + 1 : max };
}


export function positionForTime(timestamp: string, bounds: TimelineBounds) {
  const time = new Date(timestamp).getTime();
  if (!Number.isFinite(time)) return 0;
  return Math.max(
    0,
    Math.min(100, ((time - bounds.min) / (bounds.max - bounds.min)) * 100)
  );
}


export function laneMarkerClass(event: TraceEvent) {
  if (event.type === "agent_transition") {
    return `agent_transition transition-${String(event.data.phase || "enter")}`;
  }
  if (
    event.type === "tool_result"
    && /error|exception|traceback/i.test(String(event.data.content || ""))
  ) {
    return "tool_result tool_result_error";
  }
  return event.type;
}


export function eventSummary(event: TraceEvent) {
  const data = event.data;
  if (event.type === "tool_call") return `${String(data.tool || "tool")} call`;
  if (event.type === "tool_result") return `${String(data.tool || "tool")} result`;
  if (event.type === "agent_transition") {
    return `${String(data.phase || "")} ${normalizeAgent(data.agent)}`;
  }
  if (event.type === "plan_update") {
    return `plan_index ${String(data.plan_index ?? "-")}`;
  }
  if (event.type === "agent_message") {
    const meta = data.meta as {
      streamed_token_count?: number;
      stream_duration_ms?: number;
    } | undefined;
    const suffix = meta
      ? ` (stream · ${meta.streamed_token_count || 0} tokens · ${((meta.stream_duration_ms || 0) / 1000).toFixed(1)}s)`
      : "";
    return `${String(data.content || "").slice(0, 70)}${suffix}`;
  }
  if (event.type === "session_snapshot") return "baseline snapshot";
  if (event.type === "interrupt_required") return "approval required";
  if (event.type === "done") return "stream done";
  if (event.type === "error") return String(data.message || "error");
  return event.type;
}
