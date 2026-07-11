import { normalizeAgent } from "../agentColors";
import type { AgentKey, SessionState, ToolCall, TraceEvent } from "../types";
import type { ParsedSseMessage, SseEnvelope, SsePayload } from "./sseEnvelope";


export interface StreamMeta {
  token_count: number;
  stream_started_at: string;
  stream_ended_at?: string;
}

export interface StreamReducerState {
  responseId: string;
  activeAgent: AgentKey;
  meta: Partial<Record<AgentKey, StreamMeta>>;
  toolCalls: Record<string, ToolCall>;
}

export interface StreamReductionOptions {
  now: string;
  createId: () => string;
}

type RecordedEvent = Omit<TraceEvent, "id" | "seq" | "timestamp"> & {
  timestamp?: string;
};

export type StreamAction =
  | { type: "record_event"; event: RecordedEvent }
  | { type: "set_session_state"; state: Partial<SessionState> }
  | { type: "update_streaming_message"; responseId: string; agent: AgentKey; text: string; finalContent?: string }
  | { type: "add_tool_call"; toolCall: ToolCall; responseId: string | null }
  | { type: "update_tool_result"; toolCall: ToolCall; responseId: string | null }
  | { type: "protocol_warning"; event: string; data: SsePayload }
  | { type: "stream_error"; message: string };

export interface StreamReduction {
  state: StreamReducerState;
  actions: StreamAction[];
}


export function createStreamReducerState(
  responseId: string,
  activeAgent: AgentKey,
  toolCalls: Record<string, ToolCall>
): StreamReducerState {
  return {
    responseId,
    activeAgent,
    meta: {},
    toolCalls: { ...toolCalls }
  };
}


export function reduceSseMessage(
  state: StreamReducerState,
  parsed: ParsedSseMessage,
  options: StreamReductionOptions
): StreamReduction {
  if (parsed.kind === "unknown") {
    return {
      state,
      actions: [{ type: "protocol_warning", event: parsed.event, data: parsed.data }]
    };
  }
  return reduceSseEvent(state, parsed.envelope, options);
}


export function reduceSseEvent(
  state: StreamReducerState,
  envelope: SseEnvelope,
  options: StreamReductionOptions
): StreamReduction {
  const { data } = envelope;

  switch (envelope.type) {
    case "session_snapshot":
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, normalizeAgent(data.current_agent)),
        { type: "set_session_state", state: data as Partial<SessionState> }
      ]);

    case "agent_transition": {
      const agent = normalizeAgent(data.agent);
      return {
        state: { ...state, activeAgent: agent },
        actions: [
          recordEvent(state, envelope.type, data, agent),
          ...(data.phase === "enter"
            ? [{ type: "set_session_state", state: { current_agent: agent } } as StreamAction]
            : [])
        ]
      };
    }

    case "plan_update": {
      const update: Partial<SessionState> = {};
      if (Array.isArray(data.plan)) update.workflow_plan = data.plan.map(String);
      if (typeof data.plan_index === "number") update.plan_index = data.plan_index;
      if (typeof data.learning_target === "string" || data.learning_target === null) {
        update.learning_target = data.learning_target;
      }
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        { type: "set_session_state", state: update }
      ]);
    }

    case "structured_result":
      return actionsOnly(state, [
        recordEvent(
          state,
          envelope.type,
          data,
          normalizeAgent(data.node || state.activeAgent)
        )
      ]);

    case "token": {
      const agent = normalizeAgent(data.agent || state.activeAgent);
      const text = typeof data.text === "string" ? data.text : "";
      const nextState = withTokenMeta(state, agent, options.now);
      return {
        state: nextState,
        actions: [
          {
            type: "update_streaming_message",
            responseId: state.responseId,
            agent,
            text
          }
        ]
      };
    }

    case "agent_message": {
      const agent = normalizeAgent(data.agent || state.activeAgent);
      const content = typeof data.content === "string" ? data.content : "";
      if (!content.trim()) return actionsOnly(state, []);
      const meta = eventMeta(state, agent, options.now);
      return actionsOnly(state, [
        recordEvent(
          state,
          envelope.type,
          meta ? { ...data, meta } : data,
          agent
        ),
        {
          type: "update_streaming_message",
          responseId: state.responseId,
          agent,
          text: "",
          finalContent: content
        },
        { type: "set_session_state", state: { current_agent: agent } }
      ]);
    }

    case "tool_call": {
      const agent = normalizeAgent(data.agent || state.activeAgent);
      const id = typeof data.tool_call_id === "string"
        ? data.tool_call_id
        : options.createId();
      const toolCall: ToolCall = {
        id,
        agent,
        node: typeof data.node === "string" ? data.node : "",
        tool: typeof data.tool === "string" ? data.tool : "tool",
        args: data.args || {},
        result: "",
        status: "pending",
        createdAt: options.now,
        updatedAt: options.now
      };
      const actions: StreamAction[] = [
        recordEvent(state, envelope.type, data, agent),
        { type: "add_tool_call", toolCall, responseId: state.responseId }
      ];
      if (toolCall.tool === "PlanWorkflow" && isObject(data.args)) {
        const steps = data.args.steps;
        if (Array.isArray(steps)) {
          actions.push({
            type: "set_session_state",
            state: {
              workflow_plan: steps.map(String),
              plan_index: 0,
              learning_target: typeof data.args.learning_target === "string"
                ? data.args.learning_target
                : undefined
            }
          });
        }
      }
      return {
        state: {
          ...state,
          toolCalls: { ...state.toolCalls, [id]: toolCall }
        },
        actions
      };
    }

    case "tool_result": {
      const agent = normalizeAgent(data.agent || state.activeAgent);
      const id = typeof data.tool_call_id === "string"
        ? data.tool_call_id
        : options.createId();
      const existing = state.toolCalls[id];
      const content = typeof data.content === "string" ? data.content : "";
      const toolCall: ToolCall = {
        id,
        agent: existing?.agent || agent,
        node: typeof data.node === "string" ? data.node : existing?.node || "",
        tool: typeof data.tool === "string" ? data.tool : existing?.tool || "tool",
        args: existing?.args || {},
        result: content,
        status: data.status === "error" ? "error" : "done",
        createdAt: existing?.createdAt || options.now,
        updatedAt: options.now
      };
      return {
        state: {
          ...state,
          toolCalls: { ...state.toolCalls, [id]: toolCall }
        },
        actions: [
          recordEvent(state, envelope.type, data, toolCall.agent),
          { type: "update_tool_result", toolCall, responseId: state.responseId }
        ]
      };
    }

    case "interrupt_required":
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        { type: "set_session_state", state: { pending_interrupt: true } }
      ]);

    case "guardrail_blocked":
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        { type: "stream_error", message: "输入被安全规则阻止" }
      ]);

    case "no_pending_interrupt":
    case "done":
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        { type: "set_session_state", state: { pending_interrupt: false } }
      ]);

    case "error":
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        {
          type: "stream_error",
          message: typeof data.message === "string"
            ? data.message
            : "后端返回错误事件"
        }
      ]);

    default:
      return assertNever(envelope);
  }
}


function actionsOnly(
  state: StreamReducerState,
  actions: StreamAction[]
): StreamReduction {
  return { state, actions };
}


function recordEvent(
  state: StreamReducerState,
  type: string,
  data: SsePayload,
  agent: AgentKey
): StreamAction {
  return {
    type: "record_event",
    event: {
      type,
      data,
      agent,
      responseId: state.responseId
    }
  };
}


function withTokenMeta(
  state: StreamReducerState,
  agent: AgentKey,
  now: string
): StreamReducerState {
  const existing = state.meta[agent] || {
    token_count: 0,
    stream_started_at: now
  };
  return {
    ...state,
    meta: {
      ...state.meta,
      [agent]: {
        ...existing,
        token_count: existing.token_count + 1,
        stream_ended_at: now
      }
    }
  };
}


function eventMeta(
  state: StreamReducerState,
  agent: AgentKey,
  now: string
) {
  const meta = state.meta[agent];
  if (!meta) return undefined;
  const start = new Date(meta.stream_started_at).getTime();
  const end = new Date(meta.stream_ended_at || now).getTime();
  return {
    streamed_token_count: meta.token_count,
    stream_duration_ms: Number.isFinite(start) && Number.isFinite(end)
      ? Math.max(0, end - start)
      : 0,
    stream_started_at: meta.stream_started_at,
    stream_ended_at: meta.stream_ended_at
  };
}


function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}


function assertNever(value: never): never {
  throw new Error(`Unhandled SSE event: ${JSON.stringify(value)}`);
}
