import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE, getLearningOverview, getSessionState } from "./api";
import { normalizeAgent } from "./agentColors";
import { useAppStore } from "./store";
import type { AgentKey, ToolCall } from "./types";
import { uid } from "./utils";

interface StreamMeta {
  token_count: number;
  stream_started_at: string;
  stream_ended_at?: string;
}

interface StreamContext {
  id: string;
  activeAgent: AgentKey;
  meta: Map<AgentKey, StreamMeta>;
}

function parseData(raw: string) {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed === "string" && /^[\[{]/.test(parsed.trim())) {
      return JSON.parse(parsed) as Record<string, unknown>;
    }
    return typeof parsed === "object" && parsed !== null ? parsed as Record<string, unknown> : { value: parsed };
  } catch {
    return { raw, message: raw };
  }
}

function eventMeta(context: StreamContext, agent: AgentKey) {
  const meta = context.meta.get(agent);
  if (!meta) return undefined;
  const start = new Date(meta.stream_started_at).getTime();
  const end = new Date(meta.stream_ended_at || new Date()).getTime();
  return {
    streamed_token_count: meta.token_count,
    stream_duration_ms: Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : 0,
    stream_started_at: meta.stream_started_at,
    stream_ended_at: meta.stream_ended_at
  };
}

function updateTokenMeta(context: StreamContext, agent: AgentKey) {
  const now = new Date().toISOString();
  const existing = context.meta.get(agent) || { token_count: 0, stream_started_at: now };
  existing.token_count += 1;
  existing.stream_ended_at = now;
  context.meta.set(agent, existing);
}

async function refreshStateAndLearning(sessionId: string) {
  const store = useAppStore.getState();
  try {
    const [state, learning] = await Promise.all([getSessionState(sessionId), getLearningOverview()]);
    store.setSessionState(state);
    store.setLearning(learning);
  } catch (error) {
    store.addSystemMessage(`状态刷新失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

function applySseEvent(event: string, data: Record<string, unknown>, context: StreamContext) {
  const store = useAppStore.getState();

  if (event === "session_snapshot") {
    store.recordEvent({ type: event, data, agent: normalizeAgent(data.current_agent), responseId: context.id });
    store.setSessionState(data);
    return;
  }

  if (event === "agent_transition") {
    const agent = normalizeAgent(data.agent);
    store.recordEvent({ type: event, data, agent, responseId: context.id });
    if (data.phase === "enter") store.setSessionState({ current_agent: agent });
    context.activeAgent = agent;
    return;
  }

  if (event === "plan_update") {
    store.recordEvent({ type: event, data, agent: context.activeAgent, responseId: context.id });
    const update: Record<string, unknown> = {};
    if (Array.isArray(data.plan)) update.workflow_plan = data.plan;
    if (typeof data.plan_index === "number") update.plan_index = data.plan_index;
    if ("learning_target" in data) update.learning_target = data.learning_target;
    store.setSessionState(update);
    return;
  }

  if (event === "token") {
    const agent = normalizeAgent(data.agent || context.activeAgent);
    const text = typeof data.text === "string" ? data.text : "";
    updateTokenMeta(context, agent);
    store.updateStreamingMessage(context.id, agent, text);
    return;
  }

  if (event === "agent_message") {
    const agent = normalizeAgent(data.agent || context.activeAgent);
    const content = typeof data.content === "string" ? data.content : "";
    if (!content.trim()) return;
    const meta = eventMeta(context, agent);
    store.recordEvent({ type: event, data: meta ? { ...data, meta } : data, agent, responseId: context.id });
    store.updateStreamingMessage(context.id, agent, "", content);
    store.setSessionState({ current_agent: agent });
    return;
  }

  if (event === "tool_call") {
    const agent = normalizeAgent(data.agent || context.activeAgent);
    const id = typeof data.tool_call_id === "string" ? data.tool_call_id : uid();
    const toolCall: ToolCall = {
      id,
      agent,
      node: typeof data.node === "string" ? data.node : "",
      tool: typeof data.tool === "string" ? data.tool : "tool",
      args: data.args || {},
      result: "",
      status: "pending",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };
    store.recordEvent({ type: event, data, agent, responseId: context.id });
    store.addToolCall(toolCall, context.id);
    if (toolCall.tool === "PlanWorkflow" && data.args && typeof data.args === "object" && Array.isArray((data.args as { steps?: unknown }).steps)) {
      const args = data.args as { steps: unknown[]; learning_target?: unknown };
      store.setSessionState({
        workflow_plan: args.steps.map(String),
        plan_index: 0,
        learning_target: typeof args.learning_target === "string" ? args.learning_target : undefined
      });
    }
    return;
  }

  if (event === "tool_result") {
    const agent = normalizeAgent(data.agent || context.activeAgent);
    const id = typeof data.tool_call_id === "string" ? data.tool_call_id : uid();
    const existing = useAppStore.getState().toolCalls[id];
    const content = typeof data.content === "string" ? data.content : "";
    const toolCall: ToolCall = {
      id,
      agent: existing?.agent || agent,
      node: typeof data.node === "string" ? data.node : existing?.node || "",
      tool: typeof data.tool === "string" ? data.tool : existing?.tool || "tool",
      args: existing?.args || {},
      result: content,
      status: /error|exception|traceback/i.test(content) ? "error" : "done",
      createdAt: existing?.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };
    store.recordEvent({ type: event, data, agent: toolCall.agent, responseId: context.id });
    store.updateToolResult(toolCall, context.id);
    return;
  }

  if (event === "interrupt_required") {
    store.recordEvent({ type: event, data, agent: context.activeAgent, responseId: context.id });
    store.setSessionState({ pending_interrupt: true });
    return;
  }

  if (event === "no_pending_interrupt") {
    store.recordEvent({ type: event, data, agent: context.activeAgent, responseId: context.id });
    store.setSessionState({ pending_interrupt: false });
    return;
  }

  if (event === "done") {
    store.recordEvent({ type: event, data, agent: context.activeAgent, responseId: context.id });
    store.setSessionState({ pending_interrupt: false });
    return;
  }

  if (event === "error") {
    store.recordEvent({ type: event, data, agent: context.activeAgent, responseId: context.id });
    throw new Error(typeof data.message === "string" ? data.message : "后端返回错误事件");
  }
}

export function useChatStream() {
  async function run(path: "/chat" | "/chat/approve", body: Record<string, unknown>, label: string) {
    const store = useAppStore.getState();
    const sessionId = store.session.session_id;
    const context: StreamContext = {
      id: uid(),
      activeAgent: normalizeAgent(store.session.current_agent),
      meta: new Map()
    };

    store.setRunning(true, label);
    try {
      await fetchEventSource(`${API_BASE}${path}`, {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json"
        },
        body: JSON.stringify(body),
        openWhenHidden: true,
        onmessage(message) {
          applySseEvent(message.event || "message", parseData(message.data), context);
        }
      });
      store.finishResponse(context.id);
      await refreshStateAndLearning(sessionId);
    } catch (error) {
      store.finishResponse(context.id);
      store.setError(error instanceof Error ? error.message : String(error));
    } finally {
      store.setRunning(false);
    }
  }

  return {
    send(message: string) {
      const text = message.trim();
      if (!text) return;
      const store = useAppStore.getState();
      if (store.running || store.session.pending_interrupt) return;
      store.addUserMessage(text);
      store.rememberSession(store.session.session_id);
      return run("/chat", { session_id: store.session.session_id, message: text }, "生成中");
    },
    approve(approved: boolean, feedback = "") {
      const store = useAppStore.getState();
      if (store.running) return;
      return run("/chat/approve", { session_id: store.session.session_id, approved, feedback }, approved ? "继续执行" : "提交反馈");
    }
  };
}
