import { describe, expect, it, vi } from "vitest";
import { createAppStore } from "./store";
import type { KeyValueStorage, StorageFailure } from "./storage/keyValueStorage";
import type { PreferenceRepository } from "./storage/preferenceRepository";
import type { SessionRepository } from "./storage/sessionRepository";
import type {
  TranscriptRepository,
  TranscriptSnapshot
} from "./storage/transcriptRepository";


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


const TENANT = { user_id: "user-a", namespace: "docs" };


function repository(snapshot: TranscriptSnapshot | null = null) {
  return {
    load: vi.fn(() => snapshot),
    save: vi.fn(() => true),
    delete: vi.fn(() => true)
  } satisfies TranscriptRepository;
}


function persistedSnapshot(): TranscriptSnapshot {
  return {
    messages: [{
      id: "message-1",
      role: "assistant",
      agent: "parser",
      content: "cached",
      streaming: false,
      toolCallIds: [],
      createdAt: "2026-07-11T00:00:00.000Z"
    }],
    events: [
      {
        id: "token-event",
        seq: 1,
        type: "token",
        data: { text: "ignored" },
        agent: "parser",
        responseId: "response-1",
        timestamp: "2026-07-11T00:00:00.000Z"
      },
      {
        id: "message-event",
        seq: 2,
        type: "agent_message",
        data: { content: "cached" },
        agent: "parser",
        responseId: "response-1",
        timestamp: "2026-07-11T00:00:01.000Z"
      }
    ],
    toolCalls: {}
  };
}


describe("store persistence boundary", () => {
  it("composes injected session and preference repositories", () => {
    const sessions = {
      loadContext: vi.fn(() => ({
        session_id: "injected-session",
        user_id: "user-a",
        namespace: "docs"
      })),
      saveContext: vi.fn(() => true),
      loadSessions: vi.fn(() => [{
        id: "injected-session",
        user_id: "user-a",
        namespace: "docs",
        updatedAt: "2026-07-11T00:00:00.000Z"
      }]),
      saveSessions: vi.fn(() => true)
    } satisfies SessionRepository;
    const preferences = {
      loadTheme: vi.fn(() => "light" as const),
      saveTheme: vi.fn(() => true)
    } satisfies PreferenceRepository;
    const app = createAppStore({
      storage: new MemoryStorage(),
      sessionRepository: sessions,
      preferenceRepository: preferences,
      transcriptRepository: repository()
    });

    expect(app.getState().session.session_id).toBe("injected-session");
    expect(app.getState().sessions).toHaveLength(1);
    expect(app.getState().theme).toBe("light");

    app.getState().rememberSession("session-2", TENANT);
    app.getState().setTheme("dark");
    expect(sessions.saveSessions).toHaveBeenCalledOnce();
    expect(sessions.saveContext).toHaveBeenCalledWith({
      session_id: "session-2",
      ...TENANT
    });
    expect(preferences.saveTheme).toHaveBeenCalledWith("dark");
  });

  it("falls back safely for malformed session lists, contexts and themes", () => {
    const storage = new MemoryStorage();
    storage.setItem("tech-doc-agent.sessions.v2", JSON.stringify({ invalid: true }));
    storage.setItem("tech-doc-agent.context", "not-json");
    storage.setItem("tech-doc-agent.theme", "solarized");

    const app = createAppStore({
      storage,
      transcriptRepository: repository()
    });

    expect(app.getState().sessions).toEqual([]);
    expect(app.getState().theme).toBe("dark");
    expect(app.getState().session.session_id).toBeTruthy();
  });

  it("hydrates and persists only through the injected transcript repository", () => {
    const transcripts = repository(persistedSnapshot());
    const app = createAppStore({
      storage: new MemoryStorage(),
      transcriptRepository: transcripts
    });
    app.getState().resetForContext("session-1", TENANT);

    expect(app.getState().hydrateTranscript("session-1", TENANT)).toBe(true);
    expect(transcripts.load).toHaveBeenCalledWith("session-1", TENANT);
    expect(app.getState().messages[0].content).toBe("cached");
    expect(app.getState().events.map((event) => event.type)).toEqual([
      "agent_message"
    ]);

    transcripts.save.mockClear();
    app.getState().addUserMessage("new question");
    expect(transcripts.save).toHaveBeenCalledOnce();
    expect(transcripts.save).toHaveBeenCalledWith(
      "session-1",
      TENANT,
      expect.objectContaining({
        messages: expect.arrayContaining([
          expect.objectContaining({ role: "user", content: "new question" })
        ])
      })
    );
  });

  it("delegates transcript deletion without exposing storage keys to the store", () => {
    const transcripts = repository();
    const app = createAppStore({
      storage: new MemoryStorage(),
      transcriptRepository: transcripts
    });

    app.getState().deleteSession("session-1", TENANT);

    expect(transcripts.delete).toHaveBeenCalledWith("session-1", TENANT);
  });

  it("keeps UI actions usable when every browser storage operation fails", () => {
    const failure = new DOMException("quota or policy", "SecurityError");
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

    let app: ReturnType<typeof createAppStore>;
    expect(() => {
      app = createAppStore({
        storage,
        onStorageFailure: (item) => failures.push(item)
      });
    }).not.toThrow();

    expect(() => app!.getState().rememberSession("session-1", TENANT)).not.toThrow();
    expect(() => app!.getState().rememberSession("session-2", TENANT)).not.toThrow();
    expect(() => app!.getState().setTheme("light")).not.toThrow();
    expect(() => app!.getState().addUserMessage("still usable")).not.toThrow();
    expect(app!.getState().sessions.map((entry) => entry.id)).toEqual([
      "session-2",
      "session-1"
    ]);
    expect(() => app!.getState().deleteSession("session-1", TENANT)).not.toThrow();
    expect(app!.getState().theme).toBe("light");
    expect(app!.getState().sessions.map((entry) => entry.id)).toEqual(["session-2"]);
    const messages = app!.getState().messages;
    expect(messages[messages.length - 1]?.content).toBe("still usable");
    expect(failures.length).toBeGreaterThan(0);
    expect(new Set(failures.map((item) => item.operation))).toEqual(
      new Set(["read", "write", "delete"])
    );
  });
});
