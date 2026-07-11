import { describe, expect, it } from "vitest";
import type {
  KeyValueStorage,
  StorageFailure
} from "./keyValueStorage";
import { createSessionRepository } from "./sessionRepository";


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


const CONTEXT_KEY = "tech-doc-agent.context";
const LEGACY_SESSION_KEY = "tech-doc-agent.session";
const SESSIONS_KEY = "tech-doc-agent.sessions.v2";


describe("session repository", () => {
  it("loads a normalized current context before the legacy session id", () => {
    const storage = new MemoryStorage();
    storage.setItem(CONTEXT_KEY, JSON.stringify({
      session_id: "current-session",
      user_id: "user-a",
      namespace: "docs"
    }));
    storage.setItem(LEGACY_SESSION_KEY, "legacy-session");

    const repository = createSessionRepository(storage);

    expect(repository.loadContext()).toEqual({
      session_id: "current-session",
      user_id: "user-a",
      namespace: "docs"
    });
  });

  it("falls back from malformed context to legacy and then generated ids", () => {
    const storage = new MemoryStorage();
    storage.setItem(CONTEXT_KEY, JSON.stringify({ session_id: "" }));
    storage.setItem(LEGACY_SESSION_KEY, "legacy-session");
    const legacyRepository = createSessionRepository(storage, undefined, {
      createSessionId: () => "generated-session"
    });

    expect(legacyRepository.loadContext()).toEqual({
      session_id: "legacy-session",
      user_id: "default",
      namespace: "tech_docs"
    });

    storage.removeItem(LEGACY_SESSION_KEY);
    expect(legacyRepository.loadContext().session_id).toBe("generated-session");
  });

  it("filters malformed entries and normalizes tenant and missing timestamps", () => {
    const storage = new MemoryStorage();
    storage.setItem(SESSIONS_KEY, JSON.stringify([
      {
        id: "session-1",
        user_id: "user-a",
        namespace: "docs",
        updatedAt: "2026-07-10T00:00:00.000Z"
      },
      {
        id: "session-2",
        user_id: "bad value",
        namespace: "",
        updatedAt: ""
      },
      { id: "" },
      "not-an-entry"
    ]));
    const repository = createSessionRepository(storage, undefined, {
      now: () => "2026-07-11T00:00:00.000Z"
    });

    expect(repository.loadSessions()).toEqual([
      {
        id: "session-1",
        user_id: "user-a",
        namespace: "docs",
        updatedAt: "2026-07-10T00:00:00.000Z"
      },
      {
        id: "session-2",
        user_id: "default",
        namespace: "tech_docs",
        updatedAt: "2026-07-11T00:00:00.000Z"
      }
    ]);
  });

  it("persists current and legacy context plus the session directory", () => {
    const storage = new MemoryStorage();
    const repository = createSessionRepository(storage);
    const context = {
      session_id: "session-1",
      user_id: "user-a",
      namespace: "docs"
    };
    const sessions = [{
      id: "session-1",
      user_id: "user-a",
      namespace: "docs",
      updatedAt: "2026-07-11T00:00:00.000Z"
    }];

    expect(repository.saveContext(context)).toBe(true);
    expect(repository.saveSessions(sessions)).toBe(true);

    expect(JSON.parse(storage.values.get(CONTEXT_KEY) || "{}")).toEqual(context);
    expect(storage.values.get(LEGACY_SESSION_KEY)).toBe("session-1");
    expect(repository.loadSessions()).toEqual(sessions);
  });

  it("contains storage failures and reports each failed operation", () => {
    const error = new DOMException("blocked", "SecurityError");
    const storage: KeyValueStorage = {
      getItem() {
        throw error;
      },
      setItem() {
        throw error;
      },
      removeItem() {
        throw error;
      }
    };
    const failures: StorageFailure[] = [];
    const repository = createSessionRepository(
      storage,
      (failure) => failures.push(failure),
      { createSessionId: () => "fallback-session" }
    );

    expect(repository.loadContext().session_id).toBe("fallback-session");
    expect(repository.loadSessions()).toEqual([]);
    expect(repository.saveContext({
      session_id: "session-1",
      user_id: "user-a",
      namespace: "docs"
    })).toBe(false);
    expect(repository.saveSessions([])).toBe(false);
    expect(failures.map((failure) => failure.operation)).toEqual([
      "read",
      "read",
      "read",
      "write",
      "write",
      "write"
    ]);
  });
});
