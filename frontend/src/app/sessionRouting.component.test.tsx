// @vitest-environment jsdom

import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { useSessionBootstrap } from "../features/session/useSessionBootstrap";
import { createTranscriptRepository } from "../storage/transcriptRepository";
import { useAppStore, type AppStore } from "../store";
import type {
  ChatMessage,
  HistoryResponse,
  LearningOverview,
  SessionState,
  TenantScope
} from "../types";


const apiMocks = vi.hoisted(() => ({
  getLearningOverview: vi.fn(),
  getSessionHistory: vi.fn(),
  getSessionState: vi.fn()
}));

vi.mock("../shared/api/sessionApi", () => apiMocks);


const INITIAL_STATE = useAppStore.getInitialState();
const SESSION_ID = "shared-session";
const TENANT_A: TenantScope = { user_id: "user-a", namespace: "docs" };
const TENANT_B: TenantScope = { user_id: "user-b", namespace: "docs" };
const NOW = "2026-07-12T00:00:00.000Z";


function message(id: string, content: string): ChatMessage {
  return {
    id,
    role: "assistant",
    agent: "parser",
    content,
    streaming: false,
    toolCallIds: [],
    createdAt: NOW
  };
}


function resetStore(overrides: Partial<AppStore> = {}) {
  useAppStore.setState({
    ...INITIAL_STATE,
    session: {
      ...INITIAL_STATE.session,
      session_id: SESSION_ID,
      ...TENANT_A
    },
    sessions: [],
    messages: [],
    events: [],
    toolCalls: {},
    ...overrides
  }, true);
}


function Harness() {
  useSessionBootstrap(true);
  const location = useLocation();
  return <output>{location.search}</output>;
}


beforeEach(() => {
  window.localStorage.clear();
  apiMocks.getLearningOverview.mockReset();
  apiMocks.getSessionHistory.mockReset();
  apiMocks.getSessionState.mockReset();
  resetStore();
});

afterEach(() => cleanup());


it("keeps identical session ids isolated while URL switches tenant", async () => {
  const repository = createTranscriptRepository(window.localStorage);
  repository.save(SESSION_ID, TENANT_A, {
    messages: [message("message-a", "tenant-a-cache")],
    events: [],
    toolCalls: {}
  });
  repository.save(SESSION_ID, TENANT_B, {
    messages: [message("message-b", "tenant-b-cache")],
    events: [],
    toolCalls: {}
  });

  const state: SessionState = {
    session_id: SESSION_ID,
    ...TENANT_B,
    exists: true,
    pending_interrupt: false,
    learning_target: "StateGraph",
    message_count: 1,
    current_agent: "parser",
    workflow_plan: [],
    plan_index: 0
  };
  const history: HistoryResponse = {
    session_id: SESSION_ID,
    ...TENANT_B,
    learning_target: "StateGraph",
    pending_interrupt: false,
    message_count: 1,
    messages: [{
      id: "server-message",
      role: "assistant",
      kind: "message",
      content: "server-history-should-not-replace-cache",
      name: "parser"
    }]
  };
  const learning: LearningOverview = {
    ...TENANT_B,
    total: 0,
    average_score: 0,
    needs_review_count: 0,
    records: []
  };
  apiMocks.getSessionState.mockResolvedValue(state);
  apiMocks.getSessionHistory.mockResolvedValue(history);
  apiMocks.getLearningOverview.mockResolvedValue(learning);

  render(
    <MemoryRouter initialEntries={[
      `/studio?session=${SESSION_ID}&user_id=${TENANT_B.user_id}&namespace=${TENANT_B.namespace}`
    ]}>
      <Harness />
    </MemoryRouter>
  );

  await waitFor(() => {
    expect(useAppStore.getState().session).toMatchObject({
      session_id: SESSION_ID,
      ...TENANT_B
    });
    expect(useAppStore.getState().messages[0]?.content).toBe("tenant-b-cache");
  });

  expect(useAppStore.getState().messages).toHaveLength(1);
  expect(useAppStore.getState().messages[0].content).not.toContain("tenant-a");
  expect(useAppStore.getState().messages[0].content).not.toContain("server-history");
  expect(apiMocks.getSessionState).toHaveBeenCalledWith(
    SESSION_ID,
    TENANT_B,
    expect.objectContaining({ signal: expect.any(AbortSignal) })
  );
  expect(apiMocks.getSessionHistory).toHaveBeenCalledTimes(1);
  expect(apiMocks.getLearningOverview).toHaveBeenCalledTimes(1);
});
