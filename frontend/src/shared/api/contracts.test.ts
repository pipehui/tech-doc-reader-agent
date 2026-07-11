import { describe, expect, it } from "vitest";
import {
  decodeHistoryResponse,
  decodeLearningOverview,
  decodeSessionState
} from "./contracts";


describe("REST response contracts", () => {
  it("decodes the declared state/history/learning response shapes", () => {
    expect(decodeSessionState({
      session_id: "session-1",
      user_id: "user-a",
      namespace: "docs",
      exists: true,
      pending_interrupt: false,
      learning_target: null,
      message_count: 1,
      current_agent: "parser",
      workflow_plan: ["parser", "summary"],
      plan_index: 1
    })).toMatchObject({
      session_id: "session-1",
      current_agent: "parser",
      workflow_plan: ["parser", "summary"]
    });

    expect(decodeHistoryResponse({
      session_id: "session-1",
      user_id: null,
      namespace: null,
      learning_target: null,
      pending_interrupt: false,
      message_count: 1,
      messages: [{
        id: "message-1",
        role: "assistant",
        kind: "message",
        content: "hello",
        name: "parser",
        tool_call_id: null
      }]
    }).messages[0]).toMatchObject({
      id: "message-1",
      role: "assistant",
      name: "parser"
    });

    expect(decodeLearningOverview({
      user_id: "user-a",
      namespace: "docs",
      total: 1,
      average_score: 0.8,
      needs_review_count: 0,
      records: [{
        knowledge: "StateGraph",
        timestamp: "2026-07-12T00:00:00Z",
        score: 0.8,
        reviewtimes: 1,
        user_id: "user-a",
        namespace: "docs"
      }]
    }).records[0]).toMatchObject({ knowledge: "StateGraph", score: 0.8 });
  });

  it("rejects missing fields, unsupported roles and invalid nested values", () => {
    expect(() => decodeSessionState({ session_id: "session-1" }))
      .toThrow("user_id must be a string");

    expect(() => decodeHistoryResponse({
      session_id: "session-1",
      learning_target: null,
      pending_interrupt: false,
      message_count: 1,
      messages: [{
        role: "alien",
        kind: "message",
        content: "hello"
      }]
    })).toThrow("role has unsupported value alien");

    expect(() => decodeLearningOverview({
      total: 1,
      average_score: 0.8,
      needs_review_count: 0,
      records: [{
        knowledge: "StateGraph",
        timestamp: "2026-07-12T00:00:00Z",
        score: "high",
        reviewtimes: 1
      }]
    })).toThrow("score must be a finite number");
  });
});
