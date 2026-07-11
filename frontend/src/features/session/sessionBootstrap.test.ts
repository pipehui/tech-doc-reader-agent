import { describe, expect, it, vi } from "vitest";
import type {
  HistoryResponse,
  LearningOverview,
  SessionState,
  TenantScope
} from "../../types";
import {
  historyToMessages,
  loadSessionContext,
  type SessionBootstrapApi,
  type SessionBootstrapStore
} from "./sessionBootstrap";


const TENANT: TenantScope = { user_id: "user-a", namespace: "docs" };
const STATE: SessionState = {
  session_id: "session-1",
  user_id: TENANT.user_id,
  namespace: TENANT.namespace,
  exists: true,
  pending_interrupt: false,
  learning_target: "LangGraph",
  message_count: 2,
  current_agent: "parser",
  workflow_plan: [],
  plan_index: 0
};
const HISTORY: HistoryResponse = {
  session_id: STATE.session_id,
  learning_target: STATE.learning_target,
  pending_interrupt: false,
  message_count: 2,
  messages: [
    { id: "message-1", role: "user", kind: "message", content: "hello" },
    { role: "assistant", kind: "message", content: "world", name: "relation" }
  ]
};
const LEARNING: LearningOverview = {
  total: 1,
  average_score: 0.8,
  needs_review_count: 0,
  records: []
};


function api(overrides: Partial<SessionBootstrapApi> = {}): SessionBootstrapApi {
  return {
    getSessionState: async () => STATE,
    getSessionHistory: async () => HISTORY,
    getLearningOverview: async () => LEARNING,
    ...overrides
  };
}


function store(hasCachedTranscript = false) {
  return {
    hydrateTranscript: vi.fn(() => hasCachedTranscript),
    setSessionState: vi.fn(),
    setMessages: vi.fn(),
    setLearning: vi.fn(),
    addSystemMessage: vi.fn()
  } satisfies SessionBootstrapStore;
}


describe("session bootstrap use case", () => {
  it("normalizes history with deterministic message dependencies", () => {
    let nextId = 0;
    const messages = historyToMessages(HISTORY, STATE, {
      createId: () => `generated-${++nextId}`,
      now: () => "2026-07-11T00:00:00.000Z"
    });

    expect(messages).toEqual([
      expect.objectContaining({
        id: "message-1",
        role: "user",
        agent: "parser",
        createdAt: "2026-07-11T00:00:00.000Z"
      }),
      expect.objectContaining({
        id: "generated-1",
        role: "assistant",
        agent: "relation",
        createdAt: "2026-07-11T00:00:00.000Z"
      })
    ]);
  });

  it("hydrates server state but preserves a cached transcript", async () => {
    const target = store(true);
    const controller = new AbortController();

    await expect(loadSessionContext({
      sessionId: STATE.session_id,
      tenant: TENANT,
      signal: controller.signal,
      api: api(),
      store: target
    })).resolves.toBe("loaded");

    expect(target.hydrateTranscript).toHaveBeenCalledWith(STATE.session_id, TENANT);
    expect(target.setSessionState).toHaveBeenCalledWith(STATE);
    expect(target.setMessages).not.toHaveBeenCalled();
    expect(target.setLearning).toHaveBeenCalledWith(LEARNING);
  });

  it("converts history when no cached transcript exists", async () => {
    const target = store();
    const controller = new AbortController();

    await loadSessionContext({
      sessionId: STATE.session_id,
      tenant: TENANT,
      signal: controller.signal,
      api: api(),
      store: target,
      messageFactory: {
        createId: () => "generated-id",
        now: () => "2026-07-11T00:00:00.000Z"
      }
    });

    expect(target.setMessages).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ id: "generated-id", content: "world" })
      ])
    );
  });

  it("never writes a stale response even when the api ignores abort", async () => {
    let resolveState!: (state: SessionState) => void;
    const statePromise = new Promise<SessionState>((resolve) => {
      resolveState = resolve;
    });
    const target = store();
    const controller = new AbortController();
    const pending = loadSessionContext({
      sessionId: STATE.session_id,
      tenant: TENANT,
      signal: controller.signal,
      api: api({ getSessionState: async () => statePromise }),
      store: target
    });

    controller.abort();
    resolveState(STATE);

    await expect(pending).resolves.toBe("aborted");
    expect(target.setSessionState).not.toHaveBeenCalled();
    expect(target.setMessages).not.toHaveBeenCalled();
    expect(target.setLearning).not.toHaveBeenCalled();
    expect(target.addSystemMessage).not.toHaveBeenCalled();
  });

  it("reports non-abort failures through the store port", async () => {
    const target = store();
    const controller = new AbortController();

    await expect(loadSessionContext({
      sessionId: STATE.session_id,
      tenant: TENANT,
      signal: controller.signal,
      api: api({ getSessionState: async () => { throw new Error("offline"); } }),
      store: target
    })).resolves.toBe("failed");

    expect(target.addSystemMessage).toHaveBeenCalledWith("会话恢复失败：offline");
  });
});
