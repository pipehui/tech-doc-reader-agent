export type AgentKey =
  | "primary"
  | "parser"
  | "relation"
  | "explanation"
  | "examination"
  | "summary";

export type MessageRole = "user" | "assistant" | "system" | "tool";

export interface SessionState {
  session_id: string;
  exists: boolean;
  pending_interrupt: boolean;
  learning_target: string | null;
  message_count: number;
  current_agent: string | null;
  workflow_plan: string[];
  plan_index: number;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  agent: AgentKey;
  content: string;
  streaming: boolean;
  toolCallIds: string[];
  responseId?: string | null;
  createdAt: string;
}

export interface ToolCall {
  id: string;
  agent: AgentKey;
  node?: string;
  tool: string;
  args: unknown;
  result?: string;
  status: "pending" | "done" | "error";
  createdAt: string;
  updatedAt: string;
}

export interface TraceEvent {
  id: string;
  seq: number;
  type: string;
  data: Record<string, unknown>;
  agent: AgentKey;
  responseId: string | null;
  timestamp: string;
}

export interface LearningRecord {
  knowledge: string;
  timestamp: string;
  score: number;
  reviewtimes: number;
}

export interface LearningOverview {
  total: number;
  average_score: number;
  needs_review_count: number;
  records: LearningRecord[];
}

export interface HistoryItem {
  id?: string | null;
  role: MessageRole;
  kind: string;
  content: string;
  name?: string | null;
  tool_call_id?: string | null;
}

export interface HistoryResponse {
  session_id: string;
  learning_target: string | null;
  pending_interrupt: boolean;
  message_count: number;
  messages: HistoryItem[];
}
