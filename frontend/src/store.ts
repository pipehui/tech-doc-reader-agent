import { create } from "zustand";
import { normalizeAgent } from "./agentColors";
import type { AgentKey, ChatMessage, LearningOverview, SessionState, ToolCall, TraceEvent } from "./types";
import { makeSessionId, uid } from "./utils";

const SESSION_KEY = "tech-doc-agent.session";
const SESSIONS_KEY = "tech-doc-agent.sessions";
const THEME_KEY = "tech-doc-agent.theme";
const TRANSCRIPT_PREFIX = "tech-doc-agent.react.transcript.";
const TRANSCRIPT_VERSION = 1;

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
  updatedAt: string;
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
  rememberSession: (sessionId: string) => void;
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
  hydrateTranscript: (sessionId: string) => boolean;
  persistTranscript: () => void;
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
  return safeJson<SessionEntry[]>(localStorage.getItem(SESSIONS_KEY), []);
}

function transcriptKey(sessionId: string) {
  return `${TRANSCRIPT_PREFIX}${sessionId}`;
}

function initialSession(sessionId: string): SessionState {
  return {
    session_id: sessionId,
    exists: false,
    pending_interrupt: false,
    learning_target: null,
    message_count: 0,
    current_agent: "primary",
    workflow_plan: [],
    plan_index: 0
  };
}

const initialId = localStorage.getItem(SESSION_KEY) || makeSessionId();

export const useAppStore = create<AppStore>((set, get) => ({
  session: initialSession(initialId),
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

  rememberSession(sessionId) {
    const now = new Date().toISOString();
    const next = [{ id: sessionId, updatedAt: now }, ...readSessions().filter((item) => item.id !== sessionId)].slice(0, 16);
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(next));
    localStorage.setItem(SESSION_KEY, sessionId);
    set({ sessions: next });
  },

  setSessionId(sessionId) {
    get().rememberSession(sessionId);
    set({ session: initialSession(sessionId) });
  },

  setSessionState(state) {
    set((current) => ({
      session: {
        ...current.session,
        ...state,
        session_id: state.session_id || current.session.session_id,
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

  hydrateTranscript(sessionId) {
    const parsed = safeJson<PersistedTranscript | null>(localStorage.getItem(transcriptKey(sessionId)), null);
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
    localStorage.setItem(transcriptKey(state.session.session_id), JSON.stringify(payload));
  },

  resetForSession(sessionId) {
    get().rememberSession(sessionId);
    set({
      session: initialSession(sessionId),
      messages: [],
      events: [],
      toolCalls: {},
      selectedEventId: null,
      hasNewMessageContent: false
    });
  },

  newSession() {
    get().resetForSession(makeSessionId());
  },

  setTheme(theme) {
    localStorage.setItem(THEME_KEY, theme);
    set({ theme });
  }
}));
