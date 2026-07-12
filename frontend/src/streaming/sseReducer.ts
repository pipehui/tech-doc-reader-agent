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
  | {
      type: "protocol_warning";
      event: string;
      data: SsePayload;
      reason: "unknown_event" | "invalid_payload";
      error?: string;
    }
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
      actions: [{
        type: "protocol_warning",
        event: parsed.event,
        data: parsed.data,
        reason: "unknown_event"
      }]
    };
  }
  if (parsed.kind === "invalid") {
    return {
      state,
      actions: [
        {
          type: "protocol_warning",
          event: parsed.event,
          data: parsed.data,
          reason: "invalid_payload",
          error: parsed.error
        },
        {
          type: "stream_error",
          message: `SSE protocol error for ${parsed.event}: ${parsed.error}`
        }
      ]
    };
  }
  return reduceSseEvent(state, parsed.envelope, options);
}


export function reduceSseEvent(
  state: StreamReducerState,
  envelope: SseEnvelope,
  options: StreamReductionOptions
): StreamReduction {
  switch (envelope.type) {
    case "session_snapshot": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, normalizeAgent(data.current_agent)),
        { type: "set_session_state", state: data }
      ]);
    }

    case "agent_transition": {
      const data = envelope.data;
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
      const data = envelope.data;
      const update: Partial<SessionState> = {};
      if (data.plan !== undefined) update.workflow_plan = data.plan;
      if (data.plan_index !== undefined) update.plan_index = data.plan_index;
      if (data.learning_target !== undefined) {
        update.learning_target = data.learning_target;
      }
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        { type: "set_session_state", state: update }
      ]);
    }

    case "structured_result": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(
          state,
          envelope.type,
          data,
          normalizeAgent(data.node)
        )
      ]);
    }

    case "usage_update": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, normalizeAgent(data.node)),
        {
          type: "set_session_state",
          state: { budget_usage: data.usage }
        }
      ]);
    }

    case "budget_started": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, normalizeAgent(data.node)),
        {
          type: "set_session_state",
          state: {
            budget_status: "active",
            budget_termination: {},
            budget_usage: data.usage
          }
        }
      ]);
    }

    case "budget_terminated": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, normalizeAgent(data.node)),
        {
          type: "set_session_state",
          state: {
            budget_status: "terminated",
            budget_termination: data.termination,
            ...(data.usage === null ? {} : { budget_usage: data.usage })
          }
        }
      ]);
    }

    case "context_metrics_update": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, normalizeAgent(data.node)),
        {
          type: "set_session_state",
          state: { context_metrics: data.metrics }
        }
      ]);
    }

    case "provider_retry_update": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, normalizeAgent(data.node)),
        {
          type: "set_session_state",
          state: { provider_retry_usage: data.usage }
        }
      ]);
    }

    case "token": {
      const data = envelope.data;
      const agent = normalizeAgent(data.agent || state.activeAgent);
      const text = data.text;
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
      const data = envelope.data;
      const agent = normalizeAgent(data.agent || state.activeAgent);
      const content = data.content;
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
      const data = envelope.data;
      const agent = normalizeAgent(data.agent || state.activeAgent);
      const id = data.tool_call_id || options.createId();
      const toolCall: ToolCall = {
        id,
        agent,
        node: data.node,
        tool: data.tool || "tool",
        args: data.args,
        result: "",
        status: "pending",
        createdAt: options.now,
        updatedAt: options.now
      };
      const actions: StreamAction[] = [
        recordEvent(state, envelope.type, data, agent),
        { type: "add_tool_call", toolCall, responseId: state.responseId }
      ];
      if (toolCall.tool === "PlanWorkflow") {
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
      const data = envelope.data;
      const agent = normalizeAgent(data.agent || state.activeAgent);
      const id = data.tool_call_id || options.createId();
      const existing = state.toolCalls[id];
      const content = data.content;
      const errorMetadata = data.status === "error"
        ? {
            ...(data.code !== null
              ? { errorCode: data.code }
              : existing?.errorCode ? { errorCode: existing.errorCode } : {}),
            ...(data.safe_message !== null
              ? { safeMessage: data.safe_message }
              : data.error !== null ? { safeMessage: data.error }
              : existing?.safeMessage ? { safeMessage: existing.safeMessage } : {}),
            ...(data.retryable !== null
              ? { retryable: data.retryable }
              : existing?.retryable !== undefined ? { retryable: existing.retryable } : {}),
            ...(data.dependency !== null
              ? { dependency: data.dependency }
              : existing?.dependency ? { dependency: existing.dependency } : {}),
            ...(data.cause_type !== null
              ? { causeType: data.cause_type }
              : existing?.causeType ? { causeType: existing.causeType } : {})
          }
        : {};
      const toolCall: ToolCall = {
        id,
        agent: existing?.agent || agent,
        node: data.node,
        tool: data.tool || existing?.tool || "tool",
        args: existing?.args || {},
        result: content,
        ...errorMetadata,
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

    case "interrupt_required": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        { type: "set_session_state", state: { pending_interrupt: true } }
      ]);
    }

    case "guardrail_blocked": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        { type: "stream_error", message: "输入被安全规则阻止" }
      ]);
    }

    case "no_pending_interrupt":
    case "done": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        { type: "set_session_state", state: { pending_interrupt: false } }
      ]);
    }

    case "error": {
      const data = envelope.data;
      return actionsOnly(state, [
        recordEvent(state, envelope.type, data, state.activeAgent),
        {
          type: "stream_error",
          message: data.message
        }
      ]);
    }

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


function assertNever(value: never): never {
  throw new Error(`Unhandled SSE event: ${JSON.stringify(value)}`);
}
