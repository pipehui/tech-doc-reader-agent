import { create } from "zustand";
import { normalizeAgent } from "./agentColors";
import { normalizeTenant, sameTenant, sessionTenant, tenantKey } from "./tenant";
import type { AgentKey, ChatMessage, LearningOverview, SessionState, TenantScope, ToolCall, TraceEvent } from "./types";
import { makeSessionId, uid } from "./utils";

const LEGACY_SESSION_KEY = "tech-doc-agent.session";
const CONTEXT_KEY = "tech-doc-agent.context";
const SESSIONS_KEY = "tech-doc-agent.sessions.v2";
const THEME_KEY = "tech-doc-agent.theme";
const TRANSCRIPT_PREFIX = "tech-doc-agent.react.transcript.";
const TRANSCRIPT_VERSION = 2;

export const EVENT_TYPES = [
  "session_snapshot",
  "agent_message",
  "agent_transition",
  "plan_update",
  "tool_call",
  "tool_result",
  "interrupt_required",
  "no_pending_interrupt",
  "done",
  "error"
];

export interface SessionEntry {
  id: string;
  user_id: string;
  namespace: string;
  updatedAt: string;
}

interface StoredContext extends TenantScope {
  session_id: string;
}

interface PersistedTranscript {
  version: number;
  messages: ChatMessage[];
  events: TraceEvent[];
  toolCalls: Record<string, ToolCall>;
}

interface AppStore {
  session: SessionState;
  messages: ChatMessage[];
  events: TraceEvent[];
  toolCalls: Record<string, ToolCall>;
  sessions: SessionEntry[];
  learning: LearningOverview;
  running: boolean;
  runLabel: string;
  error: string;
  theme: "dark" | "light";
  selectedEventId: string | null;
  filters: Set<string>;
  recording: boolean;
  inspectorPaused: boolean;
  replayingEventId: string | null;
  showLearnerPlan: boolean;
  expandedToolIds: Set<string>;
  hasNewMessageContent: boolean;
  setSessionId: (sessionId: string) => void;
  rememberSession: (sessionId: string, tenant?: Partial<TenantScope>) => void;
  setSessionState: (state: Partial<SessionState>) => void;
  setMessages: (messages: ChatMessage[]) => void;
  setLearning: (learning: LearningOverview) => void;
  setRunning: (running: boolean, label?: string) => void;
  setError: (message: string) => void;
  addSystemMessage: (content: string) => void;
  addUserMessage: (content: string) => void;
  updateStreamingMessage: (responseId: string, agent: AgentKey, text: string, finalContent?: string) => void;
  finishResponse: (responseId: string) => void;
  addToolCall: (toolCall: ToolCall, responseId: string | null) => void;
  updateToolResult: (toolCall: ToolCall, responseId: string | null) => void;
  recordEvent: (event: Omit<TraceEvent, "id" | "seq" | "timestamp"> & { timestamp?: string }) => void;
  setSelectedEventId: (id: string | null) => void;
  toggleFilter: (eventType: string) => void;
  setRecording: (recording: boolean) => void;
  setInspectorPaused: (paused: boolean) => void;
  setReplayingEventId: (id: string | null) => void;
  setShowLearnerPlan: (show: boolean) => void;
  toggleToolExpanded: (id: string) => void;
  setHasNewMessageContent: (hasNew: boolean) => void;
  hydrateTranscript: (sessionId: string, tenant?: Partial<TenantScope>) => boolean;
  persistTranscript: () => void;
  deleteSession: (sessionId: string, tenant?: Partial<TenantScope>) => void;
  resetForContext: (sessionId: string, tenant: Partial<TenantScope>) => void;
  resetForSession: (sessionId: string) => void;
  newSession: () => void;
  setTheme: (theme: "dark" | "light") => void;
}

function safeJson<T>(raw: string | null, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function readSessions() {
  return safeJson<SessionEntry[]>(localStorage.getItem(SESSIONS_KEY), []).flatMap((entry) => {
    if (!entry || typeof entry.id !== "string" || !entry.id.trim()) return [];
    const tenant = normalizeTenant(entry);
    return [{
      id: entry.id,
      user_id: tenant.user_id,
      namespace: tenant.namespace,
      updatedAt: entry.updatedAt || new Date().toISOString()
    }];
  });
}

function readStoredContext(): StoredContext {
  const parsed = safeJson<StoredContext | null>(localStorage.getItem(CONTEXT_KEY), null);
  if (parsed && typeof parsed.session_id === "string" && parsed.session_id.trim()) {
    return {
      session_id: parsed.session_id,
      ...normalizeTenant(parsed)
    };
  }

  const legacySessionId = localStorage.getItem(LEGACY_SESSION_KEY) || makeSessionId();
  return {
    session_id: legacySessionId,
    ...normalizeTenant()
  };
}

function persistStoredContext(context: StoredContext) {
  localStorage.setItem(CONTEXT_KEY, JSON.stringify(context));
  localStorage.setItem(LEGACY_SESSION_KEY, context.session_id);
}

function transcriptKey(sessionId: string, tenant?: Partial<TenantScope>) {
  return `${TRANSCRIPT_PREFIX}${tenantKey(tenant)}::${sessionId}`;
}

function initialSession(sessionId: string, tenant?: Partial<TenantScope>): SessionState {
  const resolved = normalizeTenant(tenant);
  return {
    session_id: sessionId,
    user_id: resolved.user_id,
    namespace: resolved.namespace,
    exists: false,
    pending_interrupt: false,
    learning_target: null,
    message_count: 0,
    current_agent: "primary",
    workflow_plan: [],
    plan_index: 0
  };
}

const initialContext = readStoredContext();

export const useAppStore = create<AppStore>((set, get) => ({
  session: initialSession(initialContext.session_id, initialContext),
  messages: [],
  events: [],
  toolCalls: {},
  sessions: readSessions(),
  learning: { total: 0, average_score: 0, needs_review_count: 0, records: [] },
  running: false,
  runLabel: "就绪",
  error: "",
  theme: (localStorage.getItem(THEME_KEY) as "dark" | "light") || "dark",
  selectedEventId: null,
  filters: new Set(EVENT_TYPES),
  recording: true,
  inspectorPaused: false,
  replayingEventId: null,
  showLearnerPlan: false,
  expandedToolIds: new Set(),
  hasNewMessageContent: false,

  rememberSession(sessionId, tenant) {
    const resolved = normalizeTenant(tenant || sessionTenant(get().session));
    const now = new Date().toISOString();
    const next = [
      { id: sessionId, user_id: resolved.user_id, namespace: resolved.namespace, updatedAt: now },
      ...readSessions().filter((item) => item.id !== sessionId || !sameTenant(item, resolved))
    ].slice(0, 32);
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(next));
    persistStoredContext({
      session_id: sessionId,
      user_id: resolved.user_id,
      namespace: resolved.namespace
    });
    set({ sessions: next });
  },

  setSessionId(sessionId) {
    const tenant = sessionTenant(get().session);
    get().rememberSession(sessionId, tenant);
    set({ session: initialSession(sessionId, tenant) });
  },

  setSessionState(state) {
    set((current) => ({
      session: {
        ...current.session,
        ...state,
        session_id: state.session_id || current.session.session_id,
        ...normalizeTenant({
          user_id: state.user_id ?? current.session.user_id ?? undefined,
          namespace: state.namespace ?? current.session.namespace ?? undefined
        }),
        current_agent: normalizeAgent(state.current_agent || current.session.current_agent),
        workflow_plan: Array.isArray(state.workflow_plan)
          ? state.workflow_plan.map(normalizeAgent)
          : current.session.workflow_plan
      }
    }));
  },

  setMessages(messages) {
    set({ messages });
    get().persistTranscript();
  },

  setLearning(learning) {
    set({ learning });
  },

  setRunning(running, label = "生成中") {
    set({ running, runLabel: running ? label : "就绪", error: running ? "" : get().error });
  },

  setError(message) {
    set({ running: false, runLabel: "就绪", error: message });
    get().addSystemMessage(message);
  },

  addSystemMessage(content) {
    set((state) => ({
      messages: [
        ...state.messages,
        { id: uid(), role: "system", agent: "primary", content, streaming: false, toolCallIds: [], createdAt: new Date().toISOString() }
      ]
    }));
    get().persistTranscript();
  },

  addUserMessage(content) {
    set((state) => ({
      messages: [
        ...state.messages,
        { id: uid(), role: "user", agent: "primary", content, streaming: false, toolCallIds: [], createdAt: new Date().toISOString() }
      ],
      hasNewMessageContent: true
    }));
    get().persistTranscript();
  },

  updateStreamingMessage(responseId, agent, text, finalContent) {
    const normalized = normalizeAgent(agent);
    set((state) => {
      const messages = [...state.messages];
      let index = messages.findIndex((message) => message.responseId === responseId && message.agent === normalized && message.role === "assistant");
      if (index === -1) {
        messages.push({
          id: uid(),
          role: "assistant",
          agent: normalized,
          content: "",
          streaming: true,
          responseId,
          toolCallIds: [],
          createdAt: new Date().toISOString()
        });
        index = messages.length - 1;
      }
      const current = messages[index];
      messages[index] = {
        ...current,
        content: finalContent ?? `${current.content}${text}`,
        streaming: finalContent === undefined
      };
      return { messages, hasNewMessageContent: true };
    });
  },

  finishResponse(responseId) {
    set((state) => ({
      messages: state.messages
        .map((message) => message.responseId === responseId ? { ...message, streaming: false } : message)
        .filter((message) => message.role !== "assistant" || message.responseId !== responseId || message.content.trim() || message.toolCallIds.length)
    }));
    get().persistTranscript();
  },

  addToolCall(toolCall, responseId) {
    set((state) => {
      const messages = [...state.messages];
      if (responseId) {
        let index = messages.findIndex((message) => message.responseId === responseId && message.agent === toolCall.agent && message.role === "assistant");
        if (index === -1) {
          messages.push({
            id: uid(),
            role: "assistant",
            agent: toolCall.agent,
            content: "",
            streaming: true,
            responseId,
            toolCallIds: [],
            createdAt: new Date().toISOString()
          });
          index = messages.length - 1;
        }
        const current = messages[index];
        messages[index] = {
          ...current,
          toolCallIds: current.toolCallIds.includes(toolCall.id) ? current.toolCallIds : [...current.toolCallIds, toolCall.id]
        };
      }
      return {
        messages,
        toolCalls: { ...state.toolCalls, [toolCall.id]: toolCall },
        hasNewMessageContent: true
      };
    });
  },

  updateToolResult(toolCall, responseId) {
    get().addToolCall(toolCall, responseId);
  },

  recordEvent(event) {
    if (!get().recording || event.type === "token") return;
    set((state) => {
      const next: TraceEvent = {
        ...event,
        id: uid(),
        seq: state.events.length + 1,
        timestamp: event.timestamp || new Date().toISOString()
      };
      return {
        events: [...state.events, next].slice(-3000),
        selectedEventId: state.selectedEventId || next.id
      };
    });
    get().persistTranscript();
  },

  setSelectedEventId(id) {
    set({ selectedEventId: id });
  },

  toggleFilter(eventType) {
    set((state) => {
      const filters = new Set(state.filters);
      if (filters.has(eventType)) filters.delete(eventType);
      else filters.add(eventType);
      return { filters };
    });
  },

  setRecording(recording) {
    set({ recording });
  },

  setInspectorPaused(inspectorPaused) {
    set({ inspectorPaused });
  },

  setReplayingEventId(replayingEventId) {
    set({ replayingEventId });
  },

  setShowLearnerPlan(showLearnerPlan) {
    set({ showLearnerPlan });
  },

  toggleToolExpanded(id) {
    set((state) => {
      const expandedToolIds = new Set(state.expandedToolIds);
      if (expandedToolIds.has(id)) expandedToolIds.delete(id);
      else expandedToolIds.add(id);
      return { expandedToolIds };
    });
  },

  setHasNewMessageContent(hasNewMessageContent) {
    set({ hasNewMessageContent });
  },

  hydrateTranscript(sessionId, tenant) {
    const parsed = safeJson<PersistedTranscript | null>(localStorage.getItem(transcriptKey(sessionId, tenant || sessionTenant(get().session))), null);
    if (!parsed || parsed.version !== TRANSCRIPT_VERSION) return false;
    set({
      messages: parsed.messages || [],
      events: (parsed.events || []).filter((event) => event.type !== "token"),
      toolCalls: parsed.toolCalls || {}
    });
    return Boolean(parsed.messages?.length || parsed.events?.length);
  },

  persistTranscript() {
    const state = get();
    const payload: PersistedTranscript = {
      version: TRANSCRIPT_VERSION,
      messages: state.messages,
      events: state.events,
      toolCalls: state.toolCalls
    };
    localStorage.setItem(
      transcriptKey(state.session.session_id, sessionTenant(state.session)),
      JSON.stringify(payload)
    );
  },

  deleteSession(sessionId, tenant) {
    const resolved = normalizeTenant(tenant || sessionTenant(get().session));
    const next = readSessions().filter((item) => item.id !== sessionId || !sameTenant(item, resolved));
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(next));
    localStorage.removeItem(transcriptKey(sessionId, resolved));
    const current = readStoredContext();
    if (current.session_id === sessionId && sameTenant(current, resolved)) {
      const fallback = next.find((item) => sameTenant(item, resolved)) || next[0];
      if (fallback) {
        persistStoredContext({
          session_id: fallback.id,
          user_id: fallback.user_id,
          namespace: fallback.namespace
        });
      } else {
        persistStoredContext({
          session_id: makeSessionId(),
          user_id: resolved.user_id,
          namespace: resolved.namespace
        });
      }
    }
    set({ sessions: next });
  },

  resetForContext(sessionId, tenant) {
    const resolved = normalizeTenant(tenant);
    get().rememberSession(sessionId, resolved);
    set({
      session: initialSession(sessionId, resolved),
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
    get().resetForContext(makeSessionId(), sessionTenant(get().session));
  },

  setTheme(theme) {
    localStorage.setItem(THEME_KEY, theme);
    set({ theme });
  }
}));
