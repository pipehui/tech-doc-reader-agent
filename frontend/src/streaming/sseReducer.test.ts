import { describe, expect, it } from "vitest";
import { SSE_EVENT_TYPES } from "../sseContract";
import type { AgentKey, ToolCall } from "../types";
import { parseSseData, parseSseMessage } from "./sseEnvelope";
import type { SseEventType } from "../sseContract";
import {
  createStreamReducerState,
  reduceSseMessage
} from "./sseReducer";
import type {
  StreamAction,
  StreamReducerState,
  StreamReductionOptions
} from "./sseReducer";


const BASE_TIME = "2026-07-11T00:00:00.000Z";


function options(now = BASE_TIME): StreamReductionOptions {
  let nextId = 0;
  return {
    now,
    createId: () => `generated-${++nextId}`
  };
}


function reduce(
  state: StreamReducerState,
  type: SseEventType,
  data: Record<string, unknown>,
  reductionOptions = options()
) {
  const parsed = parseSseMessage(type, JSON.stringify(data));
  return reduceSseMessage(state, parsed, reductionOptions);
}


function initialState(
  agent: AgentKey = "primary",
  toolCalls: Record<string, ToolCall> = {}
) {
  return createStreamReducerState("response-1", agent, toolCalls);
}


describe("SSE wire parser", () => {
  it("parses object, double-encoded JSON, primitive and invalid text", () => {
    expect(parseSseData('{"text":"hello"}')).toEqual({ text: "hello" });
    expect(parseSseData(JSON.stringify('{"text":"hello"}'))).toEqual({
      text: "hello"
    });
    expect(parseSseData("42")).toEqual({ value: 42 });
    expect(parseSseData("not-json")).toEqual({
      raw: "not-json",
      message: "not-json"
    });
  });

  it("keeps unknown events forward-compatible", () => {
    expect(parseSseMessage("future_event", '{"value":1}')).toEqual({
      kind: "unknown",
      event: "future_event",
      data: { value: 1 }
    });
  });
});


describe("pure SSE reducer", () => {
  it("handles every declared event without an unhandled branch", () => {
    for (const eventType of SSE_EVENT_TYPES) {
      const result = reduce(initialState(), eventType, {});
      expect(result.actions).toBeDefined();
    }
  });

  it("maps session snapshots and transitions to record/state actions", () => {
    const snapshot = reduce(initialState(), "session_snapshot", {
      session_id: "session-1",
      current_agent: "parser",
      plan_index: 2
    });
    expect(snapshot.actions).toEqual([
      expect.objectContaining({
        type: "record_event",
        event: expect.objectContaining({ type: "session_snapshot", agent: "parser" })
      }),
      {
        type: "set_session_state",
        state: {
          session_id: "session-1",
          current_agent: "parser",
          plan_index: 2
        }
      }
    ]);

    const transition = reduce(snapshot.state, "agent_transition", {
      phase: "enter",
      agent: "relation"
    });
    expect(transition.state.activeAgent).toBe("relation");
    expect(transition.actions).toEqual([
      expect.objectContaining({
        type: "record_event",
        event: expect.objectContaining({ type: "agent_transition", agent: "relation" })
      }),
      { type: "set_session_state", state: { current_agent: "relation" } }
    ]);
  });

  it("normalizes plan updates and records structured results", () => {
    const plan = reduce(initialState("parser"), "plan_update", {
      plan: ["parser", 7],
      plan_index: 1,
      learning_target: null
    });
    expect(plan.actions[1]).toEqual({
      type: "set_session_state",
      state: {
        workflow_plan: ["parser", "7"],
        plan_index: 1,
        learning_target: null
      }
    });

    const structured = reduce(plan.state, "structured_result", {
      node: "relation",
      result_key: "relation_result",
      result: { parsed: true },
      parsed: true
    });
    expect(structured.actions).toEqual([
      expect.objectContaining({
        type: "record_event",
        event: expect.objectContaining({
          type: "structured_result",
          agent: "relation"
        })
      })
    ]);

    const usagePayload = {
      schema_version: 1,
      llm_calls: 1,
      tool_calls: 0,
      total_tokens: 120,
      estimated_cost_usd: null
    };
    const usage = reduce(plan.state, "usage_update", {
      node: "parser",
      delta: { kind: "llm", llm_calls: 1, total_tokens: 120 },
      usage: usagePayload
    });
    expect(usage.actions).toEqual([
      expect.objectContaining({
        type: "record_event",
        event: expect.objectContaining({
          type: "usage_update",
          agent: "parser"
        })
      }),
      {
        type: "set_session_state",
        state: { budget_usage: usagePayload }
      }
    ]);
  });

  it("tracks duplicate token metadata and finalizes the agent message", () => {
    const first = reduce(
      initialState(),
      "token",
      { agent: "explanation", text: "你" },
      options("2026-07-11T00:00:00.000Z")
    );
    const second = reduce(
      first.state,
      "token",
      { agent: "explanation", text: "好" },
      options("2026-07-11T00:00:01.000Z")
    );
    const message = reduce(
      second.state,
      "agent_message",
      { agent: "explanation", content: "你好" },
      options("2026-07-11T00:00:02.000Z")
    );

    expect(second.state.meta.explanation).toEqual({
      token_count: 2,
      stream_started_at: "2026-07-11T00:00:00.000Z",
      stream_ended_at: "2026-07-11T00:00:01.000Z"
    });
    expect(message.actions).toEqual([
      expect.objectContaining({
        type: "record_event",
        event: expect.objectContaining({
          type: "agent_message",
          data: expect.objectContaining({
            meta: {
              streamed_token_count: 2,
              stream_duration_ms: 1000,
              stream_started_at: "2026-07-11T00:00:00.000Z",
              stream_ended_at: "2026-07-11T00:00:01.000Z"
            }
          })
        })
      }),
      {
        type: "update_streaming_message",
        responseId: "response-1",
        agent: "explanation",
        text: "",
        finalContent: "你好"
      },
      {
        type: "set_session_state",
        state: { current_agent: "explanation" }
      }
    ]);
  });

  it("keeps missing token fields compatible and ignores blank final messages", () => {
    const token = reduce(
      initialState(),
      "token",
      {},
      options("2026-07-11T00:00:00.000Z")
    );
    expect(token.actions[0]).toEqual({
      type: "update_streaming_message",
      responseId: "response-1",
      agent: "primary",
      text: ""
    });
    expect(reduce(token.state, "agent_message", { content: "   " }).actions).toEqual([]);
  });

  it("creates PlanWorkflow tool state and stays consistent on duplicate calls", () => {
    const callData = {
      agent: "primary",
      tool: "PlanWorkflow",
      tool_call_id: "call-1",
      args: {
        steps: ["parser", "explanation"],
        learning_target: "StateGraph"
      }
    };
    const first = reduce(initialState(), "tool_call", callData);
    const duplicate = reduce(
      first.state,
      "tool_call",
      callData,
      options("2026-07-11T00:00:01.000Z")
    );

    expect(first.state.toolCalls["call-1"]).toEqual(
      expect.objectContaining({
        id: "call-1",
        tool: "PlanWorkflow",
        status: "pending"
      })
    );
    expect(first.actions[2]).toEqual({
      type: "set_session_state",
      state: {
        workflow_plan: ["parser", "explanation"],
        plan_index: 0,
        learning_target: "StateGraph"
      }
    });
    expect(Object.keys(duplicate.state.toolCalls)).toEqual(["call-1"]);
    expect(duplicate.state.toolCalls["call-1"].updatedAt).toBe(
      "2026-07-11T00:00:01.000Z"
    );
  });

  it("joins ordered and out-of-order tool results with deterministic status", () => {
    const existing: ToolCall = {
      id: "call-1",
      agent: "parser",
      node: "parser",
      tool: "read_docs",
      args: { query: "StateGraph" },
      result: "",
      status: "pending",
      createdAt: "2026-07-10T00:00:00.000Z",
      updatedAt: "2026-07-10T00:00:00.000Z"
    };
    const result = reduce(
      initialState("primary", { "call-1": existing }),
      "tool_result",
      {
        tool_call_id: "call-1",
        content: "request failed without a magic keyword",
        status: "error",
        error: "Document retrieval timed out.",
        safe_message: "Safe timeout summary.",
        code: "dependency_timeout",
        retryable: true,
        dependency: "embedding",
        cause_type: "ProviderTimeout"
      },
      options("2026-07-11T00:00:00.000Z")
    );
    expect(result.state.toolCalls["call-1"]).toEqual({
      ...existing,
      result: "request failed without a magic keyword",
      errorCode: "dependency_timeout",
      safeMessage: "Safe timeout summary.",
      retryable: true,
      dependency: "embedding",
      causeType: "ProviderTimeout",
      status: "error",
      updatedAt: "2026-07-11T00:00:00.000Z"
    });

    const outOfOrder = reduce(
      initialState(),
      "tool_result",
      {
        tool_call_id: "missing",
        tool: "web_search",
        content: "Traceback appears in successful documentation text",
        status: "success",
        error: null
      }
    );
    expect(outOfOrder.state.toolCalls.missing).toEqual(
      expect.objectContaining({
        id: "missing",
        tool: "web_search",
        args: {},
        status: "done"
      })
    );
  });

  it.each([
    ["interrupt_required", true],
    ["no_pending_interrupt", false],
    ["done", false]
  ] as const)("maps %s to pending_interrupt=%s", (eventType, pending) => {
    const result = reduce(initialState(), eventType, {});
    expect(result.actions[1]).toEqual({
      type: "set_session_state",
      state: { pending_interrupt: pending }
    });
  });

  it("turns guardrail and backend errors into explicit error actions", () => {
    const guardrail = reduce(initialState(), "guardrail_blocked", {});
    expect(guardrail.actions[guardrail.actions.length - 1]).toEqual({
      type: "stream_error",
      message: "输入被安全规则阻止"
    });

    const backend = reduce(initialState(), "error", { message: "backend failed" });
    expect(backend.actions[backend.actions.length - 1]).toEqual({
      type: "stream_error",
      message: "backend failed"
    });
  });

  it("reduces unknown events to a warning without changing state", () => {
    const state = initialState();
    const result = reduceSseMessage(
      state,
      parseSseMessage("future_event", '{"new_field":true}'),
      options()
    );

    expect(result.state).toBe(state);
    expect(result.actions).toEqual([
      {
        type: "protocol_warning",
        event: "future_event",
        data: { new_field: true }
      }
    ]);
  });
});


function actionTypes(actions: StreamAction[]) {
  return actions.map((action) => action.type);
}


it("keeps record-before-effect ordering for observable events", () => {
  const result = reduce(initialState(), "tool_result", {
    tool_call_id: "call-1",
    content: "ok",
    status: "success",
    error: null
  });
  expect(actionTypes(result.actions)).toEqual([
    "record_event",
    "update_tool_result"
  ]);
});
