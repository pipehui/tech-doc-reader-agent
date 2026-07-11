import { useLayoutEffect, useRef, type CSSProperties } from "react";
import { Copy, Download, Pause, Play } from "lucide-react";
import { agentMeta, agentStyle, normalizeAgent } from "../../agentColors";
import { EVENT_TYPES, useAppStore } from "../../store";
import { AgentBadge } from "../../shared/components/AgentBadge";
import { sessionTenant } from "../../tenant";
import type { AgentKey, TraceEvent } from "../../types";
import { formatTime } from "../../utils";
import {
  eventSummary,
  filteredEvents,
  getTimelineBounds,
  laneMarkerClass,
  positionForTime,
  type TimelineBounds
} from "./inspectorModel";
import { exportTrace } from "./traceExport";


export function Inspector() {
  return (
    <div className="inspector-layout">
      <InspectorToolbar />
      <SwimLane />
      <section className="inspector-bottom">
        <EventList />
        <EventDetail />
      </section>
    </div>
  );
}


export function InspectorToolbar() {
  const recording = useAppStore((state) => state.recording);
  const inspectorPaused = useAppStore((state) => state.inspectorPaused);
  const filters = useAppStore((state) => state.filters);
  const setRecording = useAppStore((state) => state.setRecording);
  const setInspectorPaused = useAppStore((state) => state.setInspectorPaused);
  const toggleFilter = useAppStore((state) => state.toggleFilter);
  const events = useAppStore((state) => state.events);
  const session = useAppStore((state) => state.session);

  return (
    <section className="inspector-toolbar">
      <div className="toolbar-group">
        <button
          className={`chip ${recording ? "active" : ""}`}
          type="button"
          onClick={() => setRecording(!recording)}
        >
          {recording ? <Play size={16} /> : <Pause size={16} />}
          {recording ? "录制中" : "已停止"}
        </button>
        <button
          className={`chip ${inspectorPaused ? "active" : ""}`}
          type="button"
          onClick={() => setInspectorPaused(!inspectorPaused)}
        >
          {inspectorPaused ? <Play size={16} /> : <Pause size={16} />}
          {inspectorPaused ? "继续渲染" : "暂停"}
        </button>
        <button
          className="chip"
          type="button"
          onClick={() => exportTrace(
            session.session_id,
            sessionTenant(session),
            events
          )}
        >
          <Download size={16} />导出 JSON
        </button>
      </div>
      <div className="toolbar-group">
        {EVENT_TYPES.map((type) => (
          <button
            key={type}
            className={`chip ${filters.has(type) ? "active" : ""}`}
            type="button"
            onClick={() => toggleFilter(type)}
          >
            {type}
          </button>
        ))}
      </div>
    </section>
  );
}


function SwimLane() {
  const allEvents = useAppStore((state) => state.events);
  const filters = useAppStore((state) => state.filters);
  const running = useAppStore((state) => state.running);
  const currentAgent = useAppStore(
    (state) => normalizeAgent(state.session.current_agent)
  );
  const selected = useAppStore((state) => state.selectedEventId);
  const replaying = useAppStore((state) => state.replayingEventId);
  const setSelected = useAppStore((state) => state.setSelectedEventId);
  const ref = useRef<HTMLElement | null>(null);
  const scroll = useRef({ top: 0, left: 0 });
  const events = filteredEvents(allEvents, filters);
  const bounds = getTimelineBounds(events);

  useLayoutEffect(() => {
    if (!ref.current) return;
    ref.current.scrollTop = scroll.current.top;
    ref.current.scrollLeft = scroll.current.left;
  }, [events.length, selected, replaying]);

  return (
    <section
      ref={ref}
      className="swim-lane"
      onScroll={(event) => {
        scroll.current = {
          top: event.currentTarget.scrollTop,
          left: event.currentTarget.scrollLeft
        };
      }}
    >
      {(Object.keys(agentMeta) as AgentKey[]).map((agent) => (
        <div className="lane-row" key={agent}>
          <div className="lane-label"><AgentBadge agent={agent} /></div>
          <div className="lane-track">
            {events
              .filter((event) => event.agent === agent)
              .map((event) => (
                <LaneMarker
                  key={event.id}
                  event={event}
                  bounds={bounds}
                  selected={selected === event.id || replaying === event.id}
                  onSelect={() => setSelected(event.id)}
                />
              ))}
            {running && currentAgent === agent && (
              <span className="lane-live" style={agentStyle(agent)} />
            )}
          </div>
        </div>
      ))}
    </section>
  );
}


function LaneMarker({
  event,
  bounds,
  selected,
  onSelect
}: {
  event: TraceEvent;
  bounds: TimelineBounds;
  selected: boolean;
  onSelect: () => void;
}) {
  const meta = event.data.meta as {
    stream_started_at?: string;
    streamed_token_count?: number;
  } | undefined;
  const x = positionForTime(event.timestamp, bounds);
  const segment = event.type === "agent_message" && meta?.stream_started_at
    ? {
      start: positionForTime(meta.stream_started_at, bounds),
      width: Math.max(
        1.5,
        x - positionForTime(meta.stream_started_at, bounds)
      )
    }
    : null;

  return (
    <>
      {segment && (
        <span
          className="lane-segment"
          style={{
            ...agentStyle(event.agent),
            "--x": segment.start,
            "--w": segment.width
          } as CSSProperties}
          title={`${event.agent} stream · ${meta?.streamed_token_count || 0} tokens`}
        />
      )}
      <button
        className={`lane-marker ${laneMarkerClass(event)} ${selected ? "selected" : ""}`}
        style={{ ...agentStyle(event.agent), "--x": x } as CSSProperties}
        title={`${event.type}: ${eventSummary(event)}`}
        type="button"
        onClick={onSelect}
      />
    </>
  );
}


function EventList() {
  const allEvents = useAppStore((state) => state.events);
  const filters = useAppStore((state) => state.filters);
  const selected = useAppStore((state) => state.selectedEventId);
  const setSelected = useAppStore((state) => state.setSelectedEventId);
  const events = filteredEvents(allEvents, filters);
  const ref = useRef<HTMLDivElement | null>(null);
  const top = useRef(0);

  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = top.current;
  }, [events.length, selected]);

  return (
    <div className="inspector-pane">
      <div className="panel-header"><h2 className="panel-title">事件列表</h2></div>
      <div
        className="event-list"
        ref={ref}
        onScroll={(event) => {
          top.current = event.currentTarget.scrollTop;
        }}
      >
        {events.length
          ? events.map((event) => (
            <button
              key={event.id}
              className={`event-row ${event.id === selected ? "selected" : ""}`}
              style={agentStyle(event.agent)}
              type="button"
              onClick={() => setSelected(event.id)}
            >
              <code>{formatTime(event.timestamp)}</code>
              <span>{event.agent}</span>
              <span className="event-summary">
                <span
                  className="event-type"
                  style={{
                    background: "color-mix(in srgb, var(--agent-color) 18%, transparent)",
                    color: "var(--agent-color)"
                  }}
                >
                  {event.type}
                </span>
                {" "}{eventSummary(event)}
              </span>
            </button>
          ))
          : <div className="empty-card">暂无事件</div>}
      </div>
    </div>
  );
}


function EventDetail() {
  const allEvents = useAppStore((state) => state.events);
  const filters = useAppStore((state) => state.filters);
  const selected = useAppStore((state) => state.selectedEventId);
  const events = filteredEvents(allEvents, filters);
  const event = events.find((item) => item.id === selected)
    || events[events.length - 1];
  const ref = useRef<HTMLDivElement | null>(null);
  const topByEvent = useRef<Record<string, number>>({});

  useLayoutEffect(() => {
    if (!ref.current) return;
    ref.current.scrollTop = event ? topByEvent.current[event.id] || 0 : 0;
  }, [event?.id]);

  return (
    <div
      className="detail-pane"
      ref={ref}
      onScroll={(scrollEvent) => {
        if (event) {
          topByEvent.current[event.id] = scrollEvent.currentTarget.scrollTop;
        }
      }}
    >
      <div className="panel-header"><h2 className="panel-title">事件详情</h2></div>
      {event
        ? (
          <>
            <div className="toolbar-group">
              <button
                className="text-button"
                type="button"
                onClick={() => navigator.clipboard.writeText(
                  JSON.stringify(event, null, 2)
                )}
              >
                <Copy size={16} />复制 JSON
              </button>
            </div>
            <pre className="json-block">{JSON.stringify(event, null, 2)}</pre>
          </>
        )
        : <div className="detail-empty">选择一个事件查看 payload</div>}
    </div>
  );
}
