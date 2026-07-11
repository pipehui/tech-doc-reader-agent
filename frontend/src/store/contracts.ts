import type { Theme } from "../storage/preferenceRepository";
import type { SessionEntry } from "../storage/sessionRepository";
import type {
  AgentKey,
  ChatMessage,
  LearningOverview,
  SessionState,
  TenantScope,
  ToolCall,
  TraceEvent
} from "../types";


export interface SessionSlice {
  session: SessionState;
  sessions: SessionEntry[];
  setSessionId: (sessionId: string) => void;
  rememberSession: (
    sessionId: string,
    tenant?: Partial<TenantScope>
  ) => void;
  setSessionState: (state: Partial<SessionState>) => void;
  deleteSession: (
    sessionId: string,
    tenant?: Partial<TenantScope>
  ) => void;
  resetForContext: (
    sessionId: string,
    tenant: Partial<TenantScope>
  ) => void;
  resetForSession: (sessionId: string) => void;
  newSession: () => void;
}

export interface TranscriptSlice {
  messages: ChatMessage[];
  toolCalls: Record<string, ToolCall>;
  hasNewMessageContent: boolean;
  setMessages: (messages: ChatMessage[]) => void;
  addSystemMessage: (content: string) => void;
  addUserMessage: (content: string) => void;
  updateStreamingMessage: (
    responseId: string,
    agent: AgentKey,
    text: string,
    finalContent?: string
  ) => void;
  finishResponse: (responseId: string) => void;
  addToolCall: (toolCall: ToolCall, responseId: string | null) => void;
  updateToolResult: (toolCall: ToolCall, responseId: string | null) => void;
  setHasNewMessageContent: (hasNew: boolean) => void;
  hydrateTranscript: (
    sessionId: string,
    tenant?: Partial<TenantScope>
  ) => boolean;
  persistTranscript: () => void;
}

export type TraceEventInput = Omit<
  TraceEvent,
  "id" | "seq" | "timestamp"
> & { timestamp?: string };

export interface TraceSlice {
  events: TraceEvent[];
  selectedEventId: string | null;
  filters: Set<string>;
  recording: boolean;
  inspectorPaused: boolean;
  replayingEventId: string | null;
  recordEvent: (event: TraceEventInput) => void;
  setSelectedEventId: (id: string | null) => void;
  toggleFilter: (eventType: string) => void;
  setRecording: (recording: boolean) => void;
  setInspectorPaused: (paused: boolean) => void;
  setReplayingEventId: (id: string | null) => void;
}

export interface LearningSlice {
  learning: LearningOverview;
  showLearnerPlan: boolean;
  setLearning: (learning: LearningOverview) => void;
  setShowLearnerPlan: (show: boolean) => void;
}

export interface UiSlice {
  running: boolean;
  runLabel: string;
  error: string;
  theme: Theme;
  expandedToolIds: Set<string>;
  setRunning: (running: boolean, label?: string) => void;
  setError: (message: string) => void;
  toggleToolExpanded: (id: string) => void;
  setTheme: (theme: Theme) => void;
}

export type AppStore = SessionSlice
  & TranscriptSlice
  & TraceSlice
  & LearningSlice
  & UiSlice;
