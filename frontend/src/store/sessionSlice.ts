import type { StateCreator } from "zustand";
import { normalizeAgent } from "../agentColors";
import type {
  SessionRepository,
  StoredContext
} from "../storage/sessionRepository";
import type { TranscriptRepository } from "../storage/transcriptRepository";
import { normalizeTenant, sameTenant, sessionTenant } from "../tenant";
import type { AppStore, SessionSlice } from "./contracts";
import { createInitialSession } from "./defaults";


export interface SessionSliceDependencies {
  initialContext: StoredContext;
  sessionRepository: SessionRepository;
  transcriptRepository: TranscriptRepository;
  createSessionId: () => string;
  now: () => string;
}


export function createSessionSlice(
  dependencies: SessionSliceDependencies
): StateCreator<AppStore, [], [], SessionSlice> {
  const {
    initialContext,
    sessionRepository,
    transcriptRepository,
    createSessionId,
    now
  } = dependencies;

  return (set, get) => ({
    session: createInitialSession(initialContext.session_id, initialContext),
    sessions: sessionRepository.loadSessions(),

    rememberSession(sessionId, tenant) {
      const resolved = normalizeTenant(tenant || sessionTenant(get().session));
      const next = [
        {
          id: sessionId,
          user_id: resolved.user_id,
          namespace: resolved.namespace,
          updatedAt: now()
        },
        ...get().sessions.filter(
          (item) => item.id !== sessionId || !sameTenant(item, resolved)
        )
      ].slice(0, 32);
      sessionRepository.saveSessions(next);
      sessionRepository.saveContext({
        session_id: sessionId,
        user_id: resolved.user_id,
        namespace: resolved.namespace
      });
      set({ sessions: next });
    },

    setSessionId(sessionId) {
      const tenant = sessionTenant(get().session);
      get().rememberSession(sessionId, tenant);
      set({ session: createInitialSession(sessionId, tenant) });
    },

    setSessionState(state) {
      set((current) => ({
        session: {
          ...current.session,
          ...state,
          session_id: state.session_id || current.session.session_id,
          ...normalizeTenant({
            user_id: state.user_id ?? current.session.user_id ?? undefined,
            namespace: state.namespace
              ?? current.session.namespace
              ?? undefined
          }),
          current_agent: normalizeAgent(
            state.current_agent || current.session.current_agent
          ),
          workflow_plan: Array.isArray(state.workflow_plan)
            ? state.workflow_plan.map(normalizeAgent)
            : current.session.workflow_plan
        }
      }));
    },

    deleteSession(sessionId, tenant) {
      const resolved = normalizeTenant(tenant || sessionTenant(get().session));
      const next = get().sessions.filter(
        (item) => item.id !== sessionId || !sameTenant(item, resolved)
      );
      sessionRepository.saveSessions(next);
      transcriptRepository.delete(sessionId, resolved);
      const current = sessionRepository.loadContext();
      if (current.session_id === sessionId && sameTenant(current, resolved)) {
        const fallback = next.find((item) => sameTenant(item, resolved))
          || next[0];
        sessionRepository.saveContext(fallback
          ? {
              session_id: fallback.id,
              user_id: fallback.user_id,
              namespace: fallback.namespace
            }
          : {
              session_id: createSessionId(),
              user_id: resolved.user_id,
              namespace: resolved.namespace
            });
      }
      set({ sessions: next });
    },

    resetForContext(sessionId, tenant) {
      const resolved = normalizeTenant(tenant);
      get().rememberSession(sessionId, resolved);
      set({
        session: createInitialSession(sessionId, resolved),
        messages: [],
        events: [],
        toolCalls: {},
        selectedEventId: null,
        hasNewMessageContent: false
      });
    },

    resetForSession(sessionId) {
      get().resetForContext(sessionId, sessionTenant(get().session));
    },

    newSession() {
      get().resetForContext(createSessionId(), sessionTenant(get().session));
    }
  });
}
