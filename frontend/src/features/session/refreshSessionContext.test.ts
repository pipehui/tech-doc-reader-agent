import { describe, expect, it, vi } from "vitest";
import type { LearningOverview, SessionState } from "../../types";
import { refreshSessionContext } from "./refreshSessionContext";


const STATE: SessionState = {
  session_id: "session-1",
  user_id: "user-a",
  namespace: "docs",
  exists: true,
  pending_interrupt: false,
  learning_target: null,
  message_count: 0,
  current_agent: "primary",
  workflow_plan: [],
  plan_index: 0
};
const LEARNING: LearningOverview = {
  total: 0,
  average_score: 0,
  needs_review_count: 0,
  records: []
};


function store() {
  return {
    setSessionState: vi.fn(),
    setLearning: vi.fn(),
    addSystemMessage: vi.fn()
  };
}


describe("session context refresh", () => {
  it("updates state and learning through one shared use case", async () => {
    const target = store();
    await expect(refreshSessionContext({
      sessionId: STATE.session_id,
      tenant: { user_id: "user-a", namespace: "docs" },
      api: {
        getSessionState: async () => STATE,
        getLearningOverview: async () => LEARNING
      },
      store: target
    })).resolves.toBe("loaded");

    expect(target.setSessionState).toHaveBeenCalledWith(STATE);
    expect(target.setLearning).toHaveBeenCalledWith(LEARNING);
    expect(target.addSystemMessage).not.toHaveBeenCalled();
  });

  it("turns refresh failures into a transcript system message", async () => {
    const target = store();
    await expect(refreshSessionContext({
      sessionId: STATE.session_id,
      tenant: { user_id: "user-a", namespace: "docs" },
      api: {
        getSessionState: async () => { throw new Error("offline"); },
        getLearningOverview: async () => LEARNING
      },
      store: target
    })).resolves.toBe("failed");

    expect(target.addSystemMessage).toHaveBeenCalledWith("状态刷新失败：offline");
    expect(target.setSessionState).not.toHaveBeenCalled();
    expect(target.setLearning).not.toHaveBeenCalled();
  });
});
