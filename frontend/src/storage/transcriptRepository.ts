import { tenantKey } from "../tenant";
import type {
  ChatMessage,
  TenantScope,
  ToolCall,
  TraceEvent
} from "../types";
import {
  deleteStorage,
  readStorage,
  writeStorage
} from "./keyValueStorage";
import type {
  KeyValueStorage,
  StorageFailureHandler
} from "./keyValueStorage";


const TRANSCRIPT_PREFIX = "tech-doc-agent.react.transcript.";
export const TRANSCRIPT_VERSION = 2;

export interface TranscriptSnapshot {
  messages: ChatMessage[];
  events: TraceEvent[];
  toolCalls: Record<string, ToolCall>;
}

interface PersistedTranscript extends TranscriptSnapshot {
  version: number;
}

export interface TranscriptRepository {
  load(
    sessionId: string,
    tenant?: Partial<TenantScope>
  ): TranscriptSnapshot | null;
  save(
    sessionId: string,
    tenant: Partial<TenantScope> | undefined,
    snapshot: TranscriptSnapshot
  ): boolean;
  delete(sessionId: string, tenant?: Partial<TenantScope>): boolean;
}


export function transcriptStorageKey(
  sessionId: string,
  tenant?: Partial<TenantScope>
) {
  return `${TRANSCRIPT_PREFIX}${tenantKey(tenant)}::${sessionId}`;
}


export function createTranscriptRepository(
  storage: KeyValueStorage,
  onFailure?: StorageFailureHandler
): TranscriptRepository {
  return {
    load(sessionId, tenant) {
      const raw = readStorage(
        storage,
        transcriptStorageKey(sessionId, tenant),
        onFailure
      );
      if (!raw) return null;
      try {
        const parsed = JSON.parse(raw) as unknown;
        if (!isCurrentTranscript(parsed)) return null;
        return {
          messages: Array.isArray(parsed.messages)
            ? parsed.messages as ChatMessage[]
            : [],
          events: Array.isArray(parsed.events)
            ? parsed.events as TraceEvent[]
            : [],
          toolCalls: isRecord(parsed.toolCalls)
            ? parsed.toolCalls as Record<string, ToolCall>
            : {}
        };
      } catch {
        return null;
      }
    },

    save(sessionId, tenant, snapshot) {
      const key = transcriptStorageKey(sessionId, tenant);
      try {
        const payload: PersistedTranscript = {
          ...snapshot,
          version: TRANSCRIPT_VERSION
        };
        return writeStorage(storage, key, JSON.stringify(payload), onFailure);
      } catch (error) {
        onFailure?.({ operation: "write", key, error });
        return false;
      }
    },

    delete(sessionId, tenant) {
      return deleteStorage(
        storage,
        transcriptStorageKey(sessionId, tenant),
        onFailure
      );
    }
  };
}


function isCurrentTranscript(
  value: unknown
): value is Record<string, unknown> & { version: number } {
  return isRecord(value) && value.version === TRANSCRIPT_VERSION;
}


function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
