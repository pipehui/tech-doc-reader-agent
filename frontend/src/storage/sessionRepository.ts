import { normalizeTenant } from "../tenant";
import type { TenantScope } from "../types";
import { makeSessionId } from "../utils";
import { readStorage, writeStorage } from "./keyValueStorage";
import type {
  KeyValueStorage,
  StorageFailureHandler
} from "./keyValueStorage";


const LEGACY_SESSION_KEY = "tech-doc-agent.session";
const CONTEXT_KEY = "tech-doc-agent.context";
const SESSIONS_KEY = "tech-doc-agent.sessions.v2";

export interface SessionEntry extends TenantScope {
  id: string;
  updatedAt: string;
}

export interface StoredContext extends TenantScope {
  session_id: string;
}

export interface SessionRepository {
  loadContext(): StoredContext;
  saveContext(context: StoredContext): boolean;
  loadSessions(): SessionEntry[];
  saveSessions(sessions: SessionEntry[]): boolean;
}

export interface SessionRepositoryOptions {
  createSessionId: () => string;
  now: () => string;
}


const DEFAULT_OPTIONS: SessionRepositoryOptions = {
  createSessionId: makeSessionId,
  now: () => new Date().toISOString()
};


export function createSessionRepository(
  storage: KeyValueStorage,
  onFailure?: StorageFailureHandler,
  options: Partial<SessionRepositoryOptions> = {}
): SessionRepository {
  const resolvedOptions = { ...DEFAULT_OPTIONS, ...options };

  return {
    loadContext() {
      const parsed = safeJson(
        readStorage(storage, CONTEXT_KEY, onFailure)
      );
      if (
        isRecord(parsed)
        && typeof parsed.session_id === "string"
        && parsed.session_id.trim()
      ) {
        return {
          session_id: parsed.session_id,
          ...normalizeTenant({
            user_id: stringOrUndefined(parsed.user_id),
            namespace: stringOrUndefined(parsed.namespace)
          })
        };
      }

      const legacySessionId = readStorage(
        storage,
        LEGACY_SESSION_KEY,
        onFailure
      ) || resolvedOptions.createSessionId();
      return {
        session_id: legacySessionId,
        ...normalizeTenant()
      };
    },

    saveContext(context) {
      const currentSaved = writeStorage(
        storage,
        CONTEXT_KEY,
        JSON.stringify(context),
        onFailure
      );
      const legacySaved = writeStorage(
        storage,
        LEGACY_SESSION_KEY,
        context.session_id,
        onFailure
      );
      return currentSaved && legacySaved;
    },

    loadSessions() {
      const parsed = safeJson(
        readStorage(storage, SESSIONS_KEY, onFailure)
      );
      if (!Array.isArray(parsed)) return [];
      return parsed.flatMap((entry) => normalizeSessionEntry(
        entry,
        resolvedOptions.now
      ));
    },

    saveSessions(sessions) {
      return writeStorage(
        storage,
        SESSIONS_KEY,
        JSON.stringify(sessions),
        onFailure
      );
    }
  };
}


function normalizeSessionEntry(
  value: unknown,
  now: () => string
): SessionEntry[] {
  if (!isRecord(value) || typeof value.id !== "string" || !value.id.trim()) {
    return [];
  }
  const tenant = normalizeTenant({
    user_id: stringOrUndefined(value.user_id),
    namespace: stringOrUndefined(value.namespace)
  });
  return [{
    id: value.id,
    ...tenant,
    updatedAt: typeof value.updatedAt === "string" && value.updatedAt.trim()
      ? value.updatedAt
      : now()
  }];
}


function safeJson(raw: string | null): unknown {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}


function stringOrUndefined(value: unknown) {
  return typeof value === "string" ? value : undefined;
}


function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
