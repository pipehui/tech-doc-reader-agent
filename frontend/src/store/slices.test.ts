import { describe, expect, it, vi } from "vitest";
import { createAppStore } from "../store";
import type { KeyValueStorage } from "../storage/keyValueStorage";
import { createPreferenceRepository } from "../storage/preferenceRepository";
import { createSessionRepository } from "../storage/sessionRepository";
import { createTranscriptRepository } from "../storage/transcriptRepository";
import type {
  LearningOverview,
  TenantScope,
  ToolCall,
  TraceEvent
} from "../types";


class MemoryStorage implements KeyValueStorage {
  readonly values = new Map<string, string>();

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }

  removeItem(key: string) {
    this.values.delete(key);
  }
}


const NOW = "2026-07-11T00:00:00.000Z";
const TENANT_A: TenantScope = { user_id: "user-a", namespace: "docs" };
const TENANT_B: TenantScope = { user_id: "user-b", namespace: "docs" };


function harness() {
  const storage = new MemoryStorage();
  let nextSessionId = 0;
  let nextId = 0;
  const createSessionId = () => `generated-session-${++nextSessionId}`;
  const sessionRepository = createSessionRepository(storage, undefined, {
    createSessionId,
    now: () => NOW
  });
  sessionRepository.saveContext({
    session_id: "initial-session",
    ...TENANT_A
  });
  const preferenceRepository = createPreferenceRepository(storage);
  const transcriptRepository = createTranscriptRepository(storage);
  const app = createAppStore({
    storage,
    sessionRepository,
    preferenceRepository,
    transcriptRepository,
    createId: () => `generated-id-${++nextId}`,
    createSessionId,
    now: () => NOW
  });
  return {
    app,
    preferenceRepository,
    sessionRepository,
    storage,
    transcriptRepository
  };
}


describe("session slice", () => {
  it("keeps identical session ids tenant-isolated and resets cross-slice state", () => {
    const { app } = harness();
    app.getState().addUserMessage("before reset");
    app.getState().recordEvent({
      type: "agent_message",
      data: { content: "before reset" },
      agent: "primary",
      responseId: "response-1"
    });

    app.getState().resetForContext("shared-session", TENANT_A);
    app.getState().resetForContext("shared-session", TENANT_B);

    expect(app.getState().sessions.map((entry) => ({
      id: entry.id,
      user_id: entry.user_id
    }))).toEqual([
      { id: "shared-session", user_id: "user-b" },
      { id: "shared-session", user_id: "user-a" }
    ]);
    expect(app.getState().session).toMatchObject({
      session_id: "shared-session",
      ...TENANT_B
    });
    expect(app.getState().messages).toEqual([]);
    expect(app.getState().events).toEqual([]);
    expect(app.getState().toolCalls).toEqual({});
    expect(app.getState().selectedEventId).toBeNull();

    app.getState().newSession();
    expect(app.getState().session.session_id).toBe("generated-session-1");
  });

  it("normalizes session deltas and deletes transcript with context fallback", () => {
    const { app, sessionRepository, transcriptRepository } = harness();
    const deleteTranscript = vi.spyOn(transcriptRepository, "delete");
    app.getState().resetForContext("session-1", TENANT_A);
    app.getState().resetForContext("session-2", TENANT_A);
    app.getState().setSessionState({
      current_agent: "parser",
      workflow_plan: ["relation", "not-an-agent"]
    });

    expect(app.getState().session.current_agent).toBe("parser");
    expect(app.getState().session.workflow_plan).toEqual([
      "relation",
      "primary"
    ]);

    app.getState().deleteSession("session-2", TENANT_A);
    expect(deleteTranscript).toHaveBeenCalledWith("session-2", TENANT_A);
    expect(app.getState().sessions.map((entry) => entry.id)).toEqual([
      "session-1"
    ]);
    expect(sessionRepository.loadContext()).toEqual({
      session_id: "session-1",
      ...TENANT_A
    });
  });
});


describe("transcript slice", () => {
  it("creates deterministic messages and preserves streaming finalization", () => {
    const { app, transcriptRepository } = harness();
    app.getState().addUserMessage("question");
    app.getState().addSystemMessage("notice");
    app.getState().updateStreamingMessage(
      "response-1",
      "explanation",
      "hel"
    );
    app.getState().updateStreamingMessage(
      "response-1",
      "explanation",
      "lo"
    );
    app.getState().updateStreamingMessage(
      "response-1",
      "explanation",
      "",
      "hello"
    );
    app.getState().finishResponse("response-1");

    expect(app.getState().messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      streaming: message.streaming,
      createdAt: message.createdAt
    }))).toEqual([
      {
        id: "generated-id-1",
        role: "user",
        content: "question",
        streaming: false,
        createdAt: NOW
      },
      {
        id: "generated-id-2",
        role: "system",
        content: "notice",
        streaming: false,
        createdAt: NOW
      },
      {
        id: "generated-id-3",
        role: "assistant",
        content: "hello",
        streaming: false,
        createdAt: NOW
      }
    ]);
    expect(transcriptRepository.load("initial-session", TENANT_A)?.messages)
      .toHaveLength(3);

    app.getState().updateStreamingMessage("empty-response", "primary", "");
    app.getState().finishResponse("empty-response");
    expect(app.getState().messages.some(
      (message) => message.responseId === "empty-response"
    )).toBe(false);
  });

  it("joins tools without duplicate ids and filters token events on hydrate", () => {
    const { app, transcriptRepository } = harness();
    const pending: ToolCall = {
      id: "call-1",
      agent: "parser",
      tool: "read_docs",
      args: { query: "StateGraph" },
      result: "",
      status: "pending",
      createdAt: NOW,
      updatedAt: NOW
    };
    const completed: ToolCall = {
      ...pending,
      result: "ok",
      status: "done"
    };
    app.getState().addToolCall(pending, "response-1");
    app.getState().addToolCall(pending, "response-1");
    app.getState().updateToolResult(completed, "response-1");

    expect(app.getState().messages[0].toolCallIds).toEqual(["call-1"]);
    expect(app.getState().toolCalls["call-1"].status).toBe("done");

    const events: TraceEvent[] = [
      traceEvent("token-event", "token", 1),
      traceEvent("message-event", "agent_message", 2)
    ];
    transcriptRepository.save("hydrated-session", TENANT_B, {
      messages: [],
      events,
      toolCalls: { "call-1": completed }
    });
    expect(app.getState().hydrateTranscript("hydrated-session", TENANT_B))
      .toBe(true);
    expect(app.getState().events.map((event) => event.type)).toEqual([
      "agent_message"
    ]);
  });
});


describe("trace slice", () => {
  it("filters token/paused events, sequences records and clones filters", () => {
    const { app, transcriptRepository } = harness();
    app.getState().recordEvent({
      type: "token",
      data: { text: "ignored" },
      agent: "primary",
      responseId: "response-1"
    });
    app.getState().setRecording(false);
    app.getState().recordEvent({
      type: "error",
      data: { message: "ignored" },
      agent: "primary",
      responseId: "response-1"
    });
    app.getState().setRecording(true);
    app.getState().recordEvent({
      type: "agent_message",
      data: { content: "one" },
      agent: "primary",
      responseId: "response-1"
    });
    app.getState().recordEvent({
      type: "done",
      data: {},
      agent: "primary",
      responseId: "response-1",
      timestamp: "2026-07-11T00:00:01.000Z"
    });

    expect(app.getState().events.map((event) => ({
      id: event.id,
      seq: event.seq,
      type: event.type,
      timestamp: event.timestamp
    }))).toEqual([
      {
        id: "generated-id-1",
        seq: 1,
        type: "agent_message",
        timestamp: NOW
      },
      {
        id: "generated-id-2",
        seq: 2,
        type: "done",
        timestamp: "2026-07-11T00:00:01.000Z"
      }
    ]);
    expect(app.getState().selectedEventId).toBe("generated-id-1");
    expect(transcriptRepository.load("initial-session", TENANT_A)?.events)
      .toHaveLength(2);

    const previousFilters = app.getState().filters;
    app.getState().toggleFilter("done");
    expect(app.getState().filters).not.toBe(previousFilters);
    expect(app.getState().filters.has("done")).toBe(false);
  });
});


describe("learning slice", () => {
  it("updates learning data and learner-plan visibility independently", () => {
    const { app } = harness();
    const learning: LearningOverview = {
      total: 1,
      average_score: 80,
      needs_review_count: 0,
      records: [{
        knowledge: "StateGraph",
        timestamp: NOW,
        score: 80,
        reviewtimes: 1
      }]
    };

    app.getState().setLearning(learning);
    app.getState().setShowLearnerPlan(true);

    expect(app.getState().learning).toBe(learning);
    expect(app.getState().showLearnerPlan).toBe(true);
  });
});


describe("ui slice", () => {
  it("coordinates run errors with transcript and persists UI preferences", () => {
    const {
      app,
      preferenceRepository,
      storage,
      transcriptRepository
    } = harness();
    app.getState().setError("failed");

    expect(app.getState()).toMatchObject({
      running: false,
      runLabel: "就绪",
      error: "failed"
    });
    expect(app.getState().messages[0]).toMatchObject({
      role: "system",
      content: "failed"
    });
    expect(transcriptRepository.load("initial-session", TENANT_A)?.messages)
      .toHaveLength(1);

    app.getState().setRunning(true, "生成中");
    expect(app.getState()).toMatchObject({
      running: true,
      runLabel: "生成中",
      error: ""
    });
    app.getState().toggleToolExpanded("call-1");
    app.getState().toggleFilter("done");
    app.getState().setTheme("light");
    app.getState().persistTranscript();
    expect(app.getState().expandedToolIds.has("call-1")).toBe(true);
    expect(preferenceRepository.loadTheme()).toBe("light");

    const persisted = [...storage.values.entries()].find(([key]) =>
      key.startsWith("tech-doc-agent.react.transcript.")
    );
    expect(Object.keys(JSON.parse(persisted?.[1] || "{}")).sort()).toEqual([
      "events",
      "messages",
      "toolCalls",
      "version"
    ]);
  });
});


function traceEvent(id: string, type: string, seq: number): TraceEvent {
  return {
    id,
    seq,
    type,
    data: {},
    agent: "primary",
    responseId: "response-1",
    timestamp: NOW
  };
}
