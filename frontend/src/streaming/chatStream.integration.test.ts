import { describe, expect, it, vi } from "vitest";
import { createAppStore } from "../store";
import type { KeyValueStorage } from "../storage/keyValueStorage";
import {
  createChatStream,
  type StreamTransport
} from "./chatStream";


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


function emit(
  init: Parameters<StreamTransport>[1],
  event: string,
  data: Record<string, unknown>
) {
  init.onmessage?.({ id: "", event, data: JSON.stringify(data) });
}


describe("chat stream integration", () => {
  it("runs send -> tool -> interrupt -> approve -> done through a fake SSE transport", async () => {
    let nextId = 0;
    const createId = () => `integration-${++nextId}`;
    const app = createAppStore({
      storage: new MemoryStorage(),
      createId,
      createSessionId: () => "integration-session",
      now: () => "2026-07-12T00:00:00.000Z"
    });
    app.getState().resetForContext("integration-session", {
      user_id: "integration-user",
      namespace: "integration-tests"
    });

    const stream: StreamTransport = vi.fn(async (input, init) => {
      await init.onopen?.(new Response(null, {
        status: 200,
        headers: { "content-type": "text/event-stream; charset=utf-8" }
      }));

      if (String(input).endsWith("/chat")) {
        emit(init, "tool_call", {
          agent: "parser",
          node: "parser_assistant_safe_tools",
          tool: "read_docs",
          tool_call_id: "call-1",
          args: { query: "StateGraph" }
        });
        emit(init, "tool_result", {
          agent: "parser",
          node: "parser_assistant_safe_tools",
          tool: "read_docs",
          tool_call_id: "call-1",
          content: "found",
          status: "success",
          error: null
        });
        emit(init, "interrupt_required", {
          pending: true,
          session_id: "integration-session"
        });
        return;
      }

      emit(init, "agent_message", {
        agent: "summary",
        node: "summary",
        content: "审批后继续完成"
      });
      emit(init, "done", { session_id: "integration-session" });
    });
    const refreshContext = vi.fn(async () => undefined);
    const headersForTenant = vi.fn((tenant) => ({
      "x-user-id": tenant.user_id,
      "x-namespace": tenant.namespace
    }));
    const chat = createChatStream({
      stream,
      apiBase: "/api",
      headersForTenant,
      getStore: app.getState,
      refreshContext,
      createId,
      now: () => "2026-07-12T00:00:01.000Z"
    });

    await chat.send("  explain StateGraph  ");

    expect(app.getState().messages[0]).toMatchObject({
      role: "user",
      content: "explain StateGraph"
    });
    expect(app.getState().toolCalls["call-1"]).toMatchObject({
      tool: "read_docs",
      result: "found",
      status: "done"
    });
    expect(app.getState().session.pending_interrupt).toBe(true);
    expect(app.getState().running).toBe(false);
    expect(app.getState().events.map((event) => event.type)).toEqual([
      "tool_call",
      "tool_result",
      "interrupt_required"
    ]);
    expect(chat.send("must wait for approval")).toBeUndefined();
    expect(stream).toHaveBeenCalledTimes(1);

    await chat.approve(true);

    expect(app.getState().session.pending_interrupt).toBe(false);
    expect(app.getState().messages).toEqual(expect.arrayContaining([
      expect.objectContaining({
        role: "assistant",
        agent: "summary",
        content: "审批后继续完成",
        streaming: false
      })
    ]));
    expect(app.getState().events.map((event) => event.type)).toEqual([
      "tool_call",
      "tool_result",
      "interrupt_required",
      "agent_message",
      "done"
    ]);
    expect(stream).toHaveBeenCalledTimes(2);
    expect(refreshContext).toHaveBeenCalledTimes(2);
    expect(headersForTenant).toHaveBeenCalledWith({
      user_id: "integration-user",
      namespace: "integration-tests"
    });

    const firstRequest = vi.mocked(stream).mock.calls[0];
    const secondRequest = vi.mocked(stream).mock.calls[1];
    expect(firstRequest[0]).toBe("/api/chat");
    expect(JSON.parse(String(firstRequest[1].body))).toMatchObject({
      session_id: "integration-session",
      message: "explain StateGraph",
      user_id: "integration-user",
      namespace: "integration-tests"
    });
    expect(secondRequest[0]).toBe("/api/chat/approve");
    expect(JSON.parse(String(secondRequest[1].body))).toMatchObject({
      session_id: "integration-session",
      approved: true,
      feedback: ""
    });
  });
});
