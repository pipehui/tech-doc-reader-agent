import { describe, expect, it, vi } from "vitest";
import { createJsonClient } from "./client";
import { createSessionApi } from "./sessionApi";


describe("session endpoint adapter", () => {
  it("encodes endpoint paths and applies the response decoder", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      session_id: "session/1",
      user_id: "user-a",
      namespace: "docs",
      exists: true,
      pending_interrupt: false,
      learning_target: null,
      message_count: 0,
      current_agent: null,
      workflow_plan: [],
      plan_index: 0
    }), { status: 200 }));
    const api = createSessionApi(createJsonClient({ fetchImpl }));

    await expect(api.getSessionState(
      "session/1",
      { user_id: "user-a", namespace: "docs" }
    )).resolves.toMatchObject({ session_id: "session/1", exists: true });

    expect(fetchImpl).toHaveBeenCalledWith(
      "/sessions/session%2F1/state?user_id=user-a&namespace=docs",
      expect.any(Object)
    );
  });
});
