import { describe, expect, it } from "vitest";
import type { ToolCall, TraceEvent } from "../types";
import type {
  KeyValueStorage,
  StorageFailure
} from "./keyValueStorage";
import {
  createTranscriptRepository,
  transcriptStorageKey,
  TRANSCRIPT_VERSION
} from "./transcriptRepository";
import type { TranscriptSnapshot } from "./transcriptRepository";


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


const TENANT_A = { user_id: "user-a", namespace: "docs" };
const TENANT_B = { user_id: "user-b", namespace: "docs" };


function snapshot(content = "hello"): TranscriptSnapshot {
  const toolCall: ToolCall = {
    id: "call-1",
    agent: "parser",
    tool: "read_docs",
    args: { query: "StateGraph" },
    result: "ok",
    status: "done",
    createdAt: "2026-07-11T00:00:00.000Z",
    updatedAt: "2026-07-11T00:00:01.000Z"
  };
  const event: TraceEvent = {
    id: "event-1",
    seq: 1,
    type: "tool_result",
    data: { tool_call_id: "call-1" },
    agent: "parser",
    responseId: "response-1",
    timestamp: "2026-07-11T00:00:01.000Z"
  };
  return {
    messages: [{
      id: "message-1",
      role: "assistant",
      agent: "parser",
      content,
      streaming: false,
      toolCallIds: ["call-1"],
      responseId: "response-1",
      createdAt: "2026-07-11T00:00:00.000Z"
    }],
    events: [event],
    toolCalls: { "call-1": toolCall }
  };
}


describe("transcript repository", () => {
  it("round-trips versioned snapshots and isolates identical session ids by tenant", () => {
    const storage = new MemoryStorage();
    const repository = createTranscriptRepository(storage);

    expect(repository.save("shared-session", TENANT_A, snapshot("tenant-a"))).toBe(true);
    expect(repository.save("shared-session", TENANT_B, snapshot("tenant-b"))).toBe(true);

    expect(repository.load("shared-session", TENANT_A)?.messages[0].content)
      .toBe("tenant-a");
    expect(repository.load("shared-session", TENANT_B)?.messages[0].content)
      .toBe("tenant-b");
    expect(storage.values.size).toBe(2);
    expect(JSON.parse(
      storage.values.get(transcriptStorageKey("shared-session", TENANT_A)) || "{}"
    )).toMatchObject({ version: TRANSCRIPT_VERSION });
  });

  it("rejects malformed and stale payloads while normalizing missing collections", () => {
    const storage = new MemoryStorage();
    const repository = createTranscriptRepository(storage);
    const key = transcriptStorageKey("session-1", TENANT_A);

    storage.setItem(key, "not-json");
    expect(repository.load("session-1", TENANT_A)).toBeNull();

    storage.setItem(key, JSON.stringify({ version: TRANSCRIPT_VERSION - 1 }));
    expect(repository.load("session-1", TENANT_A)).toBeNull();

    storage.setItem(key, JSON.stringify({ version: TRANSCRIPT_VERSION }));
    expect(repository.load("session-1", TENANT_A)).toEqual({
      messages: [],
      events: [],
      toolCalls: {}
    });
  });

  it("deletes only the requested tenant/session transcript", () => {
    const storage = new MemoryStorage();
    const repository = createTranscriptRepository(storage);
    repository.save("shared-session", TENANT_A, snapshot("tenant-a"));
    repository.save("shared-session", TENANT_B, snapshot("tenant-b"));

    expect(repository.delete("shared-session", TENANT_A)).toBe(true);

    expect(repository.load("shared-session", TENANT_A)).toBeNull();
    expect(repository.load("shared-session", TENANT_B)?.messages[0].content)
      .toBe("tenant-b");
  });

  it("contains read, quota and security failures instead of throwing", () => {
    const failure = new DOMException("blocked", "SecurityError");
    const storage: KeyValueStorage = {
      getItem() {
        throw failure;
      },
      setItem() {
        throw failure;
      },
      removeItem() {
        throw failure;
      }
    };
    const failures: StorageFailure[] = [];
    const repository = createTranscriptRepository(
      storage,
      (item) => failures.push(item)
    );

    expect(repository.load("session-1", TENANT_A)).toBeNull();
    expect(repository.save("session-1", TENANT_A, snapshot())).toBe(false);
    expect(repository.delete("session-1", TENANT_A)).toBe(false);
    expect(failures.map((item) => item.operation)).toEqual([
      "read",
      "write",
      "delete"
    ]);
  });

  it("reports serialization failures from cyclic tool arguments", () => {
    const storage = new MemoryStorage();
    const failures: StorageFailure[] = [];
    const repository = createTranscriptRepository(
      storage,
      (item) => failures.push(item)
    );
    const value = snapshot();
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    value.toolCalls["call-1"].args = cyclic;

    expect(repository.save("session-1", TENANT_A, value)).toBe(false);
    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatchObject({ operation: "write" });
  });
});
