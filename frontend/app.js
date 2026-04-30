const AGENTS = {
  primary: { label: "primary", color: "#4F46E5", icon: "路由" },
  parser: { label: "parser", color: "#D97706", icon: "解析" },
  relation: { label: "relation", color: "#7C3AED", icon: "关联" },
  explanation: { label: "explanation", color: "#059669", icon: "讲解" },
  examination: { label: "examination", color: "#E11D48", icon: "测验" },
  summary: { label: "summary", color: "#475569", icon: "总结" },
};

const EVENT_TYPES = [
  "session_snapshot",
  "agent_message",
  "agent_transition",
  "plan_update",
  "tool_call",
  "tool_result",
  "interrupt_required",
  "no_pending_interrupt",
  "done",
  "error",
];

const storage = {
  session: "tech-doc-agent.session",
  sessions: "tech-doc-agent.sessions",
  theme: "tech-doc-agent.theme",
  transcriptPrefix: "tech-doc-agent.spa.transcript.",
  version: 1,
};

const app = document.querySelector("#app");
const toastHost = document.querySelector("#toastHost");

const store = {
  session: {
    id: "",
    exists: false,
    pending_interrupt: false,
    learning_target: null,
    message_count: 0,
    current_agent: "primary",
    workflow_plan: [],
    plan_index: 0,
  },
  messages: [],
  events: [],
  toolCalls: {},
  sessions: [],
  learning: {
    total: 0,
    average_score: 0,
    needs_review_count: 0,
    records: [],
  },
  ui: {
    view: "studio",
    theme: "dark",
    running: false,
    runLabel: "就绪",
    error: "",
    selectedEventId: null,
    expandedToolIds: new Set(),
    filters: new Set(EVENT_TYPES),
    inspectorPaused: false,
    recording: true,
    replayingEventId: null,
    showLearnerPlan: false,
    messagesAtBottom: true,
    hasNewContent: false,
    messageScrollTop: 0,
    forceScrollToBottom: false,
    inspectorEventScrollTop: 0,
    inspectorDetailScrollTop: 0,
    inspectorSwimScrollTop: 0,
    inspectorSwimScrollLeft: 0,
  },
  stream: {
    abortController: null,
    context: null,
  },
};

let scheduledRenderTimer = null;
let scheduledStreamRenderTimer = null;
let scheduledScrollFrame = null;

function uid() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

function makeSessionId() {
  const day = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  return `doc-${day}-${Math.random().toString(16).slice(2, 8)}`;
}

function normalizeAgent(agent) {
  if (!agent) return "primary";
  const raw = String(agent).trim();
  if (raw === "primary_assistant") return "primary";
  for (const key of Object.keys(AGENTS)) {
    if (raw === key || raw.startsWith(`${key}_assistant`) || raw.startsWith(`${key}_assistant_`)) {
      return key;
    }
  }
  if (raw.includes("parser")) return "parser";
  if (raw.includes("relation")) return "relation";
  if (raw.includes("explanation")) return "explanation";
  if (raw.includes("examination")) return "examination";
  if (raw.includes("summary")) return "summary";
  return raw;
}

function agentMeta(agent) {
  return AGENTS[normalizeAgent(agent)] || AGENTS.primary;
}

function styleForAgent(agent) {
  return `--agent-color:${agentMeta(agent).color}`;
}

function currentPathView() {
  const path = window.location.pathname.replace(/^\/+/, "").split("/")[0];
  return ["studio", "inspector", "learner"].includes(path) ? path : "studio";
}

function setTheme(theme) {
  store.ui.theme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = store.ui.theme;
  localStorage.setItem(storage.theme, store.ui.theme);
}

function transcriptKey(sessionId = store.session.id) {
  return `${storage.transcriptPrefix}${sessionId}`;
}

function safeJson(value, fallback = null) {
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function readSessions() {
  const parsed = safeJson(localStorage.getItem(storage.sessions), []);
  return Array.isArray(parsed) ? parsed : [];
}

function rememberSession(sessionId) {
  if (!sessionId) return;
  const now = new Date().toISOString();
  const existing = readSessions().filter((item) => item.id !== sessionId);
  const next = [{ id: sessionId, updatedAt: now }, ...existing].slice(0, 16);
  store.sessions = next;
  localStorage.setItem(storage.sessions, JSON.stringify(next));
  localStorage.setItem(storage.session, sessionId);
}

function setSessionId(sessionId, options = {}) {
  store.session.id = sessionId || makeSessionId();
  rememberSession(store.session.id);
  if (!options.keepUrl) syncUrl();
}

function syncUrl() {
  const url = new URL(window.location.href);
  const nextPath = `/${store.ui.view}`;
  url.pathname = nextPath;
  url.searchParams.set("session", store.session.id);
  window.history.replaceState({}, "", url);
}

function navigate(view) {
  store.ui.view = view;
  const url = new URL(window.location.href);
  url.pathname = `/${view}`;
  url.searchParams.set("session", store.session.id);
  window.history.pushState({}, "", url);
  render();
}

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  toastHost.append(toast);
  window.setTimeout(() => toast.remove(), 2200);
}

function setRunning(running, label = "生成中") {
  store.ui.running = running;
  store.ui.runLabel = running ? label : "就绪";
  if (!running) store.ui.error = "";
  if (!running && document.querySelector("[data-messages]") && store.ui.view !== "inspector") {
    renderStreamFrame();
  } else {
    render();
  }
}

function setError(message) {
  store.ui.running = false;
  store.ui.error = message || "请求失败";
  addSystemMessage(store.ui.error);
  render();
}

function persistTranscript() {
  if (!store.session.id) return;
  const payload = {
    version: storage.version,
    savedAt: new Date().toISOString(),
    messages: store.messages,
    events: store.events,
    toolCalls: store.toolCalls,
  };
  localStorage.setItem(transcriptKey(), JSON.stringify(payload));
}

function hydrateTranscript(sessionId) {
  const parsed = safeJson(localStorage.getItem(transcriptKey(sessionId)), null);
  if (!parsed || parsed.version !== storage.version) return false;
  store.messages = Array.isArray(parsed.messages) ? parsed.messages : [];
  store.events = Array.isArray(parsed.events) ? parsed.events.filter((event) => event.type !== "token") : [];
  store.toolCalls = parsed.toolCalls && typeof parsed.toolCalls === "object" ? parsed.toolCalls : {};
  store.ui.selectedEventId = store.events.at(-1)?.id || null;
  return store.messages.length > 0 || store.events.length > 0;
}

function addSystemMessage(content) {
  store.messages.push({
    id: uid(),
    role: "system",
    agent: "primary",
    content,
    streaming: false,
    toolCallIds: [],
    createdAt: new Date().toISOString(),
  });
  persistTranscript();
}

function formatTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function relativeTime(value) {
  const date = value ? new Date(String(value).replace(" ", "T")) : null;
  if (!date || Number.isNaN(date.getTime())) return "时间未知";
  const diff = Date.now() - date.getTime();
  const days = Math.max(0, Math.floor(diff / 86400000));
  if (days === 0) return "今天";
  if (days < 31) return `${days} 天前`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} 个月前`;
  return `${Math.floor(months / 12)} 年前`;
}

function daysSince(value) {
  const date = value ? new Date(String(value).replace(" ", "T")) : null;
  if (!date || Number.isNaN(date.getTime())) return 999;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000));
}

function scoreTone(score) {
  if (score < 0.6) return "#E11D48";
  if (score < 0.8) return "#D97706";
  return "#16A34A";
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function pretty(value) {
  if (value === undefined || value === null || value === "") return "(empty)";
  if (typeof value === "string") {
    const parsed = safeJson(value, null);
    return parsed && typeof parsed === "object" ? JSON.stringify(parsed, null, 2) : value;
  }
  return JSON.stringify(value, null, 2);
}

function renderMarkdown(container, text) {
  container.replaceChildren();
  const source = String(text || "");
  if (!source.trim()) {
    container.textContent = "";
    return;
  }

  const parts = source.split(/```/g);
  parts.forEach((part, index) => {
    if (index % 2 === 1) {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      const lines = part.replace(/^\w+\n/, "");
      code.textContent = lines.trim();
      pre.append(code);
      container.append(pre);
      return;
    }

    const lines = part.split(/\n{2,}/);
    for (const block of lines) {
      if (!block.trim()) continue;
      const p = document.createElement("p");
      p.textContent = block.trim();
      container.append(p);
    }
  });
}

function icon(name, size = 16) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("viewBox", "0 0 24 24");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#icon-${name}`);
  svg.append(use);
  return svg;
}

function button(className, label, iconName, onClick, attrs = {}) {
  const el = document.createElement("button");
  el.className = className;
  el.type = "button";
  if (attrs.title) el.title = attrs.title;
  if (attrs.disabled) el.disabled = true;
  if (iconName) el.append(icon(iconName));
  if (label) el.append(document.createTextNode(label));
  if (onClick) el.addEventListener("click", onClick);
  return el;
}

function agentBadge(agent) {
  const normalized = normalizeAgent(agent);
  const span = document.createElement("span");
  span.className = "agent-badge";
  span.style.cssText = styleForAgent(normalized);
  const dot = document.createElement("i");
  dot.className = "agent-dot";
  span.append(dot, document.createTextNode(agentMeta(normalized).label));
  return span;
}

function parsePayload(raw) {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed === "string" && /^[\[{]/.test(parsed.trim())) {
      return JSON.parse(parsed);
    }
    if (parsed && typeof parsed === "object") return parsed;
    return { value: parsed, message: String(parsed) };
  } catch {
    return { raw, message: raw };
  }
}

function parseSseBlock(block) {
  let event = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    if (field === "data") dataLines.push(value);
  }
  return { event, data: parsePayload(dataLines.join("\n")) };
}

async function fetchJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function formatHttpError(response, payload) {
  if (payload && typeof payload === "object") {
    if (payload.error === "guardrail_blocked") {
      const findings = Array.isArray(payload.findings) ? payload.findings.join(", ") : "";
      return findings ? `输入被安全策略拦截：${findings}` : "输入被安全策略拦截。";
    }
    if (typeof payload.message === "string" && payload.message.trim()) return payload.message;
    if (typeof payload.error === "string" && payload.error.trim()) return payload.error;
  }
  if (typeof payload === "string" && payload.trim()) return payload;
  return `${response.status} ${response.statusText}`;
}

async function readHttpError(response) {
  const contentType = response.headers.get("content-type") || "";
  try {
    if (contentType.includes("application/json")) {
      return formatHttpError(response, await response.json());
    }
    return formatHttpError(response, await response.text());
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

async function postSse(path, body, onEvent) {
  const controller = new AbortController();
  store.stream.abortController = controller;
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  });

  if (!response.ok) {
    throw new Error(await readHttpError(response));
  }
  if (!response.body) throw new Error("浏览器没有返回可读取的 SSE 响应流。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (block.trim()) await onEvent(parseSseBlock(block));
      boundary = buffer.indexOf("\n\n");
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) await onEvent(parseSseBlock(buffer));
}

function createResponseContext() {
  return {
    id: uid(),
    segments: new Map(),
    streamMeta: new Map(),
    activeAgent: normalizeAgent(store.session.current_agent || "primary"),
  };
}

function getSegment(context, agent) {
  const normalized = normalizeAgent(agent || context.activeAgent || "primary");
  if (context.segments.has(normalized)) return context.segments.get(normalized);
  const message = {
    id: uid(),
    role: "assistant",
    agent: normalized,
    content: "",
    streaming: true,
    responseId: context.id,
    toolCallIds: [],
    createdAt: new Date().toISOString(),
  };
  store.messages.push(message);
  context.segments.set(normalized, message);
  context.activeAgent = normalized;
  return message;
}

function finishContext(context) {
  if (!context) return;
  for (const message of context.segments.values()) {
    message.streaming = false;
  }
  store.messages = store.messages.filter((message) => {
    if (message.role !== "assistant" || message.responseId !== context.id) return true;
    return message.content.trim() || (message.toolCallIds || []).length > 0;
  });
  persistTranscript();
}

function recordEvent(type, data, context) {
  if (!store.ui.recording) return null;
  const event = {
    id: uid(),
    seq: store.events.length + 1,
    type,
    data: data || {},
    agent: normalizeAgent(data?.agent || data?.current_agent || store.session.current_agent || context?.activeAgent || "primary"),
    responseId: context?.id || null,
    timestamp: new Date().toISOString(),
  };
  store.events.push(event);
  store.ui.selectedEventId = store.ui.selectedEventId || event.id;
  if (store.events.length > 3000) store.events = store.events.slice(-3000);
  return event;
}

function shouldRenderStreamFrame() {
  return !(store.ui.view === "inspector" && store.ui.inspectorPaused);
}

function markMessageContentChanged() {
  store.ui.hasNewContent = true;
}

function updateTokenMeta(context, agent, text) {
  if (!context) return;
  const normalized = normalizeAgent(agent || context.activeAgent);
  const now = new Date().toISOString();
  const existing = context.streamMeta.get(normalized) || {
    token_count: 0,
    stream_started_at: now,
  };
  existing.token_count += text ? 1 : 0;
  existing.stream_ended_at = now;
  context.streamMeta.set(normalized, existing);
}

function eventMetaForAgent(context, agent) {
  if (!context) return null;
  const meta = context.streamMeta.get(normalizeAgent(agent));
  if (!meta) return null;
  const startedAt = new Date(meta.stream_started_at).getTime();
  const endedAt = new Date(meta.stream_ended_at || new Date()).getTime();
  return {
    streamed_token_count: meta.token_count,
    stream_duration_ms: Number.isFinite(startedAt) && Number.isFinite(endedAt)
      ? Math.max(0, endedAt - startedAt)
      : 0,
    stream_started_at: meta.stream_started_at,
    stream_ended_at: meta.stream_ended_at,
  };
}

function mergeSessionSnapshot(data) {
  store.session = {
    ...store.session,
    ...data,
    current_agent: normalizeAgent(data.current_agent || store.session.current_agent || "primary"),
    workflow_plan: Array.isArray(data.workflow_plan) ? data.workflow_plan.map(normalizeAgent) : store.session.workflow_plan,
    plan_index: Number.isFinite(data.plan_index) ? data.plan_index : store.session.plan_index,
    pending_interrupt: Boolean(data.pending_interrupt),
  };
}

function attachToolToMessage(toolCall, context) {
  let message = null;
  if (context) message = getSegment(context, toolCall.agent);
  if (!message) {
    message = [...store.messages].reverse().find((item) => item.role === "assistant" && normalizeAgent(item.agent) === toolCall.agent);
  }
  if (!message) {
    message = {
      id: uid(),
      role: "assistant",
      agent: toolCall.agent,
      content: "",
      streaming: false,
      toolCallIds: [],
      createdAt: new Date().toISOString(),
    };
    store.messages.push(message);
  }
  message.toolCallIds = message.toolCallIds || [];
  if (!message.toolCallIds.includes(toolCall.id)) message.toolCallIds.push(toolCall.id);
}

function applyStreamEvent(parsed, context) {
  const type = parsed.event;
  const data = parsed.data || {};

  if (type === "session_snapshot") {
    recordEvent(type, data, context);
    mergeSessionSnapshot(data);
    return;
  }

  if (type === "agent_transition") {
    recordEvent(type, data, context);
    const agent = normalizeAgent(data.agent);
    if (data.phase === "enter") store.session.current_agent = agent;
    if (context) context.activeAgent = agent;
    return;
  }

  if (type === "plan_update") {
    recordEvent(type, data, context);
    if (Array.isArray(data.plan)) store.session.workflow_plan = data.plan.map(normalizeAgent);
    if (Number.isFinite(data.plan_index)) store.session.plan_index = data.plan_index;
    if (data.learning_target !== undefined) store.session.learning_target = data.learning_target;
    return;
  }

  if (type === "token") {
    const segment = getSegment(context, data.agent);
    segment.content += data.text || "";
    segment.streaming = true;
    context.activeAgent = segment.agent;
    updateTokenMeta(context, segment.agent, data.text || "");
    markMessageContentChanged();
    return;
  }

  if (type === "agent_message") {
    const agent = normalizeAgent(data.agent || context?.activeAgent);
    const content = data.content || "";
    if (!content.trim()) return;
    const meta = eventMetaForAgent(context, agent);
    recordEvent(type, meta ? { ...data, meta } : data, context);
    const segment = context ? getSegment(context, agent) : null;
    if (segment) {
      segment.content = content;
      segment.streaming = false;
      if (data.message_id) segment.id = data.message_id;
    } else {
      store.messages.push({
        id: data.message_id || uid(),
        role: "assistant",
        agent,
        content,
        streaming: false,
        toolCallIds: [],
        createdAt: new Date().toISOString(),
      });
    }
    store.session.current_agent = agent;
    markMessageContentChanged();
    return;
  }

  if (type === "tool_call") {
    recordEvent(type, data, context);
    const id = data.tool_call_id || uid();
    const toolCall = {
      id,
      agent: normalizeAgent(data.agent || context?.activeAgent),
      node: data.node || "",
      tool: data.tool || "tool",
      args: data.args || {},
      result: store.toolCalls[id]?.result || "",
      status: "pending",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    store.toolCalls[id] = toolCall;
    attachToolToMessage(toolCall, context);
    if (data.tool === "PlanWorkflow" && Array.isArray(data.args?.steps)) {
      store.session.workflow_plan = data.args.steps.map(normalizeAgent);
      store.session.plan_index = 0;
      if (data.args.learning_target) store.session.learning_target = data.args.learning_target;
    }
    markMessageContentChanged();
    return;
  }

  if (type === "tool_result") {
    recordEvent(type, data, context);
    const id = data.tool_call_id || uid();
    const existing = store.toolCalls[id] || {
      id,
      agent: normalizeAgent(data.agent || context?.activeAgent),
      tool: data.tool || "tool",
      args: {},
      createdAt: new Date().toISOString(),
    };
    const toolCall = {
      ...existing,
      agent: normalizeAgent(existing.agent || data.agent),
      node: data.node || existing.node || "",
      tool: data.tool || existing.tool || "tool",
      result: data.content || "",
      status: "done",
      updatedAt: new Date().toISOString(),
    };
    store.toolCalls[id] = toolCall;
    attachToolToMessage(toolCall, context);
    markMessageContentChanged();
    return;
  }

  if (type === "interrupt_required") {
    recordEvent(type, data, context);
    store.session.pending_interrupt = true;
    return;
  }

  if (type === "no_pending_interrupt") {
    recordEvent(type, data, context);
    store.session.pending_interrupt = false;
    showToast("当前没有等待审批的工具调用");
    return;
  }

  if (type === "done") {
    recordEvent(type, data, context);
    store.session.pending_interrupt = false;
    return;
  }

  if (type === "error") {
    recordEvent(type, data, context);
    throw new Error(data.message || "后端返回错误事件。");
  }
}

function lastPendingToolCall() {
  return Object.values(store.toolCalls)
    .filter((item) => item.status === "pending")
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))[0] || null;
}

async function loadSession() {
  const sessionId = store.session.id;
  hydrateTranscript(sessionId);
  render();
  try {
    const [state, history] = await Promise.all([
      fetchJson(`/sessions/${encodeURIComponent(sessionId)}/state`),
      fetchJson(`/sessions/${encodeURIComponent(sessionId)}/history?include_tools=true`),
    ]);
    mergeSessionSnapshot(state);
    if (store.messages.length === 0 && Array.isArray(history.messages)) {
      store.messages = history.messages.map((item) => ({
        id: item.id || uid(),
        role: item.role,
        agent: normalizeAgent(item.name || state.current_agent || "primary"),
        content: item.content || "",
        streaming: false,
        toolCallIds: [],
        createdAt: new Date().toISOString(),
      }));
    }
    persistTranscript();
  } catch (error) {
    showToast(`会话恢复失败：${error.message || error}`);
  }
  render();
}

async function loadLearning(options = {}) {
  try {
    store.learning = await fetchJson("/learning/overview");
  } catch (error) {
    showToast(`学习记录加载失败：${error.message || error}`);
  }
  if (options.stable && document.querySelector("[data-messages]") && store.ui.view !== "inspector") {
    renderStreamFrame();
  } else {
    render();
  }
}

async function refreshSessionStateStable() {
  try {
    const state = await fetchJson(`/sessions/${encodeURIComponent(store.session.id)}/state`);
    mergeSessionSnapshot(state);
  } catch (error) {
    showToast(`状态刷新失败：${error.message || error}`);
  }

  if (document.querySelector("[data-messages]") && store.ui.view !== "inspector") {
    renderStreamFrame();
  } else {
    render();
  }
}

async function sendMessage(text) {
  const message = String(text || "").trim();
  if (!message) return;
  if (store.ui.running || store.session.pending_interrupt) {
    showToast(store.session.pending_interrupt ? "请先处理待审批操作" : "当前流式响应还在进行中");
    return;
  }

  const context = createResponseContext();
  store.stream.context = context;
  store.messages.push({
    id: uid(),
    role: "user",
    agent: "primary",
    content: message,
    streaming: false,
    toolCallIds: [],
    createdAt: new Date().toISOString(),
  });
  markMessageContentChanged();
  rememberSession(store.session.id);
  persistTranscript();
  setRunning(true, "生成中");

  try {
    await postSse("/chat", { session_id: store.session.id, message }, async (event) => {
      applyStreamEvent(event, context);
      if (shouldRenderStreamFrame()) {
        requestStreamRender(event.event === "token" ? 100 : 0);
      }
    });
    finishContext(context);
    await loadLearning({ stable: true });
    await refreshSessionStateStable();
  } catch (error) {
    finishContext(context);
    setError(error.name === "AbortError" ? "请求已取消" : error.message || String(error));
  } finally {
    store.stream.context = null;
    store.stream.abortController = null;
    setRunning(false);
  }
}

async function sendApproval(approved, feedback = "") {
  if (store.ui.running) return;
  const context = createResponseContext();
  store.stream.context = context;
  setRunning(true, approved ? "继续执行" : "提交反馈");
  try {
    await postSse("/chat/approve", {
      session_id: store.session.id,
      approved,
      feedback,
    }, async (event) => {
      applyStreamEvent(event, context);
      if (shouldRenderStreamFrame()) {
        requestStreamRender(event.event === "token" ? 100 : 0);
      }
    });
    finishContext(context);
    await loadLearning({ stable: true });
    await refreshSessionStateStable();
  } catch (error) {
    finishContext(context);
    setError(error.message || String(error));
  } finally {
    store.stream.context = null;
    store.stream.abortController = null;
    setRunning(false);
  }
}

function newSession() {
  store.messages = [];
  store.events = [];
  store.toolCalls = {};
  store.ui.selectedEventId = null;
  store.session = {
    id: makeSessionId(),
    exists: false,
    pending_interrupt: false,
    learning_target: null,
    message_count: 0,
    current_agent: "primary",
    workflow_plan: [],
    plan_index: 0,
  };
  rememberSession(store.session.id);
  syncUrl();
  render();
}

function updateSessionInput(value) {
  const next = String(value || "").trim();
  if (!next || next === store.session.id) return;
  store.messages = [];
  store.events = [];
  store.toolCalls = {};
  store.ui.selectedEventId = null;
  setSessionId(next);
  loadSession();
}

function filteredEvents() {
  return store.events.filter((event) => event.type !== "token" && store.ui.filters.has(event.type));
}

function eventSummary(event) {
  const data = event.data || {};
  if (event.type === "tool_call") return `${data.tool || "tool"} call`;
  if (event.type === "tool_result") return `${data.tool || "tool"} result`;
  if (event.type === "agent_transition") return `${data.phase} ${normalizeAgent(data.agent)}`;
  if (event.type === "plan_update") return `plan_index ${data.plan_index ?? "-"}`;
  if (event.type === "agent_message") {
    const meta = data.meta;
    const suffix = meta
      ? ` (stream · ${meta.streamed_token_count} tokens · ${(meta.stream_duration_ms / 1000).toFixed(1)}s)`
      : "";
    return `${String(data.content || "").slice(0, 70)}${suffix}`;
  }
  if (event.type === "session_snapshot") return "baseline snapshot";
  if (event.type === "interrupt_required") return "approval required";
  if (event.type === "done") return "stream done";
  if (event.type === "error") return data.message || "error";
  return event.type;
}

function requestRender(delay = 100) {
  if (scheduledRenderTimer !== null) return;
  scheduledRenderTimer = window.setTimeout(() => {
    scheduledRenderTimer = null;
    render();
  }, delay);
}

function requestStreamRender(delay = 100) {
  if (scheduledStreamRenderTimer !== null) return;
  scheduledStreamRenderTimer = window.setTimeout(() => {
    scheduledStreamRenderTimer = null;
    renderStreamFrame();
  }, delay);
}

function isMessagesAtBottom(el) {
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

function scheduleScrollToBottom(el) {
  if (!el || scheduledScrollFrame !== null) return;
  scheduledScrollFrame = window.requestAnimationFrame(() => {
    scheduledScrollFrame = null;
    el.scrollTop = el.scrollHeight;
  });
}

function restoreMessageScroll(previous) {
  const el = document.querySelector("[data-messages]");
  if (!el) return;

  el.addEventListener("scroll", () => {
    store.ui.messageScrollTop = el.scrollTop;
    const atBottom = isMessagesAtBottom(el);
    store.ui.messagesAtBottom = atBottom;
    if (atBottom) {
      store.ui.hasNewContent = false;
      updateNewContentButton(el.closest(".chat-pane"));
    }
  }, { passive: true });

  if (store.ui.forceScrollToBottom) {
    store.ui.forceScrollToBottom = false;
    store.ui.hasNewContent = false;
    scheduleScrollToBottom(el);
    return;
  }

  el.scrollTop = previous?.top || store.ui.messageScrollTop || 0;
  store.ui.messagesAtBottom = isMessagesAtBottom(el);
  updateNewContentButton(el.closest(".chat-pane"));
}

function render() {
  if (scheduledStreamRenderTimer !== null) {
    window.clearTimeout(scheduledStreamRenderTimer);
    scheduledStreamRenderTimer = null;
  }
  if (scheduledRenderTimer !== null) {
    window.clearTimeout(scheduledRenderTimer);
    scheduledRenderTimer = null;
  }
  const previousMessagesEl = document.querySelector("[data-messages]");
  const previousInspectorScroll = getInspectorScroll();
  const previousScroll = previousMessagesEl
    ? {
      top: previousMessagesEl.scrollTop,
      atBottom: store.ui.forceScrollToBottom || isMessagesAtBottom(previousMessagesEl),
    }
    : {
      top: store.ui.messageScrollTop,
      atBottom: store.ui.messagesAtBottom,
    };
  store.ui.messageScrollTop = previousScroll.top;
  store.ui.messagesAtBottom = previousScroll.atBottom;

  document.documentElement.dataset.theme = store.ui.theme;
  app.replaceChildren(renderShell());
  queueMicrotask(() => {
    restoreMessageScroll(previousScroll);
    restoreInspectorScroll(previousInspectorScroll);
  });
}

function renderStreamFrame(options = {}) {
  if (store.ui.view === "inspector") {
    renderInspectorFrame();
    return;
  }

  const messagesEl = document.querySelector("[data-messages]");
  if (!messagesEl) {
    render();
    return;
  }

  const chatPane = messagesEl.closest(".chat-pane");
  const previousTop = messagesEl.scrollTop;

  updateTopbarStatus();
  updateStudioSidePanels();
  updateApprovalAndComposer(chatPane);

  renderMessagesInto(messagesEl);

  if (options.forceScroll || store.ui.forceScrollToBottom) {
    store.ui.forceScrollToBottom = false;
    store.ui.hasNewContent = false;
    scheduleScrollToBottom(messagesEl);
  } else {
    messagesEl.scrollTop = previousTop;
    store.ui.messagesAtBottom = isMessagesAtBottom(messagesEl);
  }

  updateNewContentButton(chatPane);
}

function renderMessagesInto(messagesEl) {
  messagesEl.replaceChildren();
  if (store.messages.length === 0) {
    messagesEl.append(renderEmptyState());
    return;
  }
  store.messages.forEach((message, index) => messagesEl.append(renderMessage(message, index)));
}

function getInspectorScroll() {
  const eventList = document.querySelector("[data-inspector-events]");
  const detailPane = document.querySelector("[data-inspector-detail]");
  const swimLane = document.querySelector("[data-inspector-swim]");
  return {
    eventTop: eventList ? eventList.scrollTop : store.ui.inspectorEventScrollTop,
    detailTop: detailPane ? detailPane.scrollTop : store.ui.inspectorDetailScrollTop,
    swimTop: swimLane ? swimLane.scrollTop : store.ui.inspectorSwimScrollTop,
    swimLeft: swimLane ? swimLane.scrollLeft : store.ui.inspectorSwimScrollLeft,
  };
}

function restoreInspectorScroll(previous = {}) {
  const eventList = document.querySelector("[data-inspector-events]");
  if (eventList) {
    eventList.scrollTop = previous.eventTop ?? store.ui.inspectorEventScrollTop;
    eventList.addEventListener("scroll", () => {
      store.ui.inspectorEventScrollTop = eventList.scrollTop;
    }, { passive: true });
  }

  const detailPane = document.querySelector("[data-inspector-detail]");
  if (detailPane) {
    detailPane.scrollTop = previous.detailTop ?? store.ui.inspectorDetailScrollTop;
    detailPane.addEventListener("scroll", () => {
      store.ui.inspectorDetailScrollTop = detailPane.scrollTop;
    }, { passive: true });
  }

  const swimLane = document.querySelector("[data-inspector-swim]");
  if (swimLane) {
    swimLane.scrollTop = previous.swimTop ?? store.ui.inspectorSwimScrollTop;
    swimLane.scrollLeft = previous.swimLeft ?? store.ui.inspectorSwimScrollLeft;
    swimLane.addEventListener("scroll", () => {
      store.ui.inspectorSwimScrollTop = swimLane.scrollTop;
      store.ui.inspectorSwimScrollLeft = swimLane.scrollLeft;
    }, { passive: true });
  }
}

function renderInspectorFrame(options = {}) {
  if (store.ui.view !== "inspector") {
    render();
    return;
  }

  updateTopbarStatus();

  const swimLane = document.querySelector("[data-inspector-swim]");
  if (swimLane) {
    const top = swimLane.scrollTop;
    const left = swimLane.scrollLeft;
    swimLane.replaceWith(renderSwimLane());
    const next = document.querySelector("[data-inspector-swim]");
    if (next) {
      next.scrollTop = top;
      next.scrollLeft = left;
    }
  }

  const eventList = document.querySelector("[data-inspector-events]");
  if (eventList) {
    const top = eventList.scrollTop;
    renderEventListInto(eventList);
    eventList.scrollTop = top;
    store.ui.inspectorEventScrollTop = top;
  }

  const detailPane = document.querySelector("[data-inspector-detail]");
  if (detailPane) {
    const visibleEvents = filteredEvents();
    const event = visibleEvents.find((item) => item.id === store.ui.selectedEventId) || visibleEvents.at(-1);
    if (options.forceDetail || detailPane.dataset.eventId !== (event?.id || "")) {
      const top = detailPane.scrollTop;
      renderEventDetailInto(detailPane);
      detailPane.scrollTop = top;
      store.ui.inspectorDetailScrollTop = top;
    }
  }
}

function updateNewContentButton(chatPane) {
  if (!chatPane) return;
  chatPane.querySelectorAll(".new-content-button").forEach((buttonEl) => buttonEl.remove());
  const messagesEl = chatPane.querySelector("[data-messages]");
  if (messagesEl) store.ui.messagesAtBottom = isMessagesAtBottom(messagesEl);
  if (store.ui.messagesAtBottom || !store.ui.hasNewContent) return;
  chatPane.append(renderNewContentButton());
}

function renderNewContentButton() {
  return button("new-content-button", "↓ 有新内容", "", () => {
    store.ui.messagesAtBottom = true;
    store.ui.hasNewContent = false;
    store.ui.forceScrollToBottom = true;
    renderStreamFrame({ forceScroll: true });
  });
}

function updateApprovalAndComposer(chatPane) {
  if (!chatPane) return;
  const approval = chatPane.querySelector(".approval-drawer");
  if (approval) approval.replaceWith(renderApprovalDrawer());
  const composer = chatPane.querySelector(".composer");
  if (composer) composer.replaceWith(renderComposer());
}

function updateStudioSidePanels() {
  if (store.ui.view === "studio") {
    const rail = document.querySelector(".rail");
    if (rail) rail.replaceWith(renderStudioRail());
    const observer = document.querySelector(".observer");
    if (observer) observer.replaceWith(renderObserver());
  }
}

function updateTopbarStatus() {
  const status = document.querySelector(".status-pill");
  if (!status) return;
  const runClass = store.ui.error ? "error" : store.ui.running ? "running" : "";
  status.className = `status-pill ${runClass}`;
  status.textContent = store.ui.error ? "错误" : store.ui.runLabel;
}

function renderShell() {
  const shell = document.createElement("div");
  shell.className = "app-shell";
  shell.append(renderTopbar());

  const frame = document.createElement("main");
  frame.className = "view-frame";
  if (store.ui.view === "inspector") frame.append(renderInspector());
  else if (store.ui.view === "learner") frame.append(renderLearner());
  else frame.append(renderStudio());
  shell.append(frame);
  return shell;
}

function renderTopbar() {
  const topbar = document.createElement("header");
  topbar.className = "topbar";

  const brand = document.createElement("div");
  brand.className = "brand";
  const mark = document.createElement("div");
  mark.className = "brand-mark";
  mark.textContent = "TD";
  const brandText = document.createElement("div");
  brandText.innerHTML = '<p class="eyebrow">LangGraph Agent</p><h1 class="brand-title">技术文档研读助手</h1>';
  brand.append(mark, brandText);

  const switcher = document.createElement("nav");
  switcher.className = "view-switcher";
  [
    ["studio", "Studio", "layers"],
    ["inspector", "Inspector", "activity"],
    ["learner", "Learner", "book"],
  ].forEach(([view, label, iconName]) => {
    const tab = button(`view-tab ${store.ui.view === view ? "active" : ""}`, label, iconName, () => navigate(view));
    switcher.append(tab);
  });

  const actions = document.createElement("div");
  actions.className = "topbar-actions";
  const sessionControl = document.createElement("label");
  sessionControl.className = "session-control";
  const sessionLabel = document.createElement("span");
  sessionLabel.textContent = "Session";
  const input = document.createElement("input");
  input.value = store.session.id;
  input.spellcheck = false;
  input.addEventListener("change", () => updateSessionInput(input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    }
  });
  sessionControl.append(sessionLabel, input);

  const runClass = store.ui.error ? "error" : store.ui.running ? "running" : "";
  const status = document.createElement("span");
  status.className = `status-pill ${runClass}`;
  status.textContent = store.ui.error ? "错误" : store.ui.runLabel;

  actions.append(
    sessionControl,
    button("icon-button", "", "copy", async () => {
      try {
        await navigator.clipboard.writeText(store.session.id);
        showToast("已复制 session id");
      } catch {
        showToast("复制失败");
      }
    }, { title: "复制 session id" }),
    button("icon-button", "", "plus", newSession, { title: "新建会话" }),
    button("icon-button", "", store.ui.theme === "dark" ? "sun" : "moon", () => {
      setTheme(store.ui.theme === "dark" ? "light" : "dark");
      render();
    }, { title: "切换主题" }),
    status,
  );

  topbar.append(brand, switcher, actions);
  return topbar;
}

function renderStudio() {
  const grid = document.createElement("div");
  grid.className = "studio-grid";
  grid.append(renderStudioRail(), renderChatPane({ mode: "studio" }), renderObserver());
  return grid;
}

function renderStudioRail() {
  const rail = document.createElement("aside");
  rail.className = "rail";

  const panel = document.createElement("section");
  panel.className = "panel";
  panel.innerHTML = '<div class="panel-header"><h2 class="panel-title">当前会话</h2></div>';
  const dl = document.createElement("dl");
  dl.className = "state-grid";
  [
    ["Agent", normalizeAgent(store.session.current_agent)],
    ["消息", String(store.session.message_count || store.messages.length)],
    ["目标", store.session.learning_target || "-"],
    ["审批", store.session.pending_interrupt ? "待确认" : "无"],
  ].forEach(([label, value]) => {
    const cell = document.createElement("div");
    cell.className = "state-cell";
    cell.innerHTML = `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`;
    dl.append(cell);
  });
  panel.append(dl);

  const sessionPanel = document.createElement("section");
  sessionPanel.className = "panel session-list";
  sessionPanel.innerHTML = '<div class="panel-header"><h2 class="panel-title">会话列表</h2></div>';
  const list = document.createElement("div");
  list.className = "session-list";
  const sessions = store.sessions.length ? store.sessions : [{ id: store.session.id, updatedAt: new Date().toISOString() }];
  sessions.forEach((item) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `session-item ${item.id === store.session.id ? "active" : ""}`;
    row.innerHTML = `<strong>${escapeHtml(item.id)}</strong><span>${escapeHtml(relativeTime(item.updatedAt))}</span>`;
    row.addEventListener("click", () => updateSessionInput(item.id));
    list.append(row);
  });
  sessionPanel.append(list);

  const graph = document.createElement("section");
  graph.className = "panel";
  graph.innerHTML = '<h2 class="panel-title">架构提示</h2><p class="meta-line">Studio 显示同一条 SSE 流中的消息、工具调用、计划推进和审批状态。</p>';

  rail.append(panel, sessionPanel, graph);
  return rail;
}

function renderObserver() {
  const observer = document.createElement("aside");
  observer.className = "observer";

  const plan = document.createElement("section");
  plan.className = "panel";
  plan.innerHTML = '<div class="panel-header"><h2 class="panel-title">当前计划</h2></div>';
  plan.append(renderPlanStepper());

  const target = document.createElement("section");
  target.className = "panel";
  target.innerHTML = `<h2 class="panel-title">学习目标</h2><p class="meta-line">${escapeHtml(store.session.learning_target || "当前会话尚未设定学习目标")}</p>`;

  const tools = document.createElement("section");
  tools.className = "panel tool-timeline";
  tools.innerHTML = '<div class="panel-header"><h2 class="panel-title">Tool 活动</h2></div>';
  const list = document.createElement("div");
  list.className = "tool-timeline";
  const toolEvents = store.events.filter((event) => event.type === "tool_call" || event.type === "tool_result").slice(-12).reverse();
  if (!toolEvents.length) {
    const empty = document.createElement("div");
    empty.className = "empty-card";
    empty.textContent = "暂无工具活动";
    list.append(empty);
  } else {
    toolEvents.forEach((event) => list.append(renderToolTimelineItem(event)));
  }
  tools.append(list);

  observer.append(plan, target, tools);
  return observer;
}

function renderToolTimelineItem(event) {
  const item = document.createElement("article");
  item.className = "tool-timeline-item";
  const data = event.data || {};
  item.innerHTML = `
    <strong>${escapeHtml(data.tool || event.type)}</strong>
    <code>${escapeHtml(normalizeAgent(data.agent || event.agent))} · ${escapeHtml(formatTime(event.timestamp))}</code>
    <p class="meta-line">${escapeHtml(event.type === "tool_call" ? pretty(data.args).slice(0, 140) : String(data.content || "").slice(0, 140))}</p>
  `;
  return item;
}

function renderChatPane(options = {}) {
  const pane = document.createElement("section");
  pane.className = "chat-pane";
  if (options.mode === "learner" && store.ui.showLearnerPlan) pane.classList.add("with-plan");

  const header = document.createElement("div");
  header.className = "chat-header";
  const title = document.createElement("div");
  title.innerHTML = `<p class="eyebrow">${options.mode === "learner" ? "Learner Chat" : "Studio"}</p><h2 class="section-title">${escapeHtml(store.session.learning_target || store.session.id)}</h2>`;
  const actions = document.createElement("div");
  actions.className = "toolbar-group";
  actions.append(
    button("text-button", "刷新", "refresh", () => {
      loadSession();
      loadLearning();
    }),
  );
  if (options.mode === "learner") {
    const toggle = button("text-button", store.ui.showLearnerPlan ? "隐藏编排" : "显示编排", "layers", () => {
      store.ui.showLearnerPlan = !store.ui.showLearnerPlan;
      render();
    });
    actions.append(toggle);
  }
  header.append(title, actions);
  pane.append(header);

  if (options.mode === "learner" && store.ui.showLearnerPlan) {
    const plan = document.createElement("div");
    plan.className = "panel";
    plan.append(renderPlanStepper());
    pane.append(plan);
  }

  if (options.mode === "learner" && normalizeAgent(store.session.current_agent) === "examination") {
    pane.append(renderQuizTakeover());
  } else {
    const messages = document.createElement("div");
    messages.className = "messages";
    messages.dataset.messages = "true";
    if (store.messages.length === 0) messages.append(renderEmptyState());
    else store.messages.forEach((message, index) => messages.append(renderMessage(message, index)));
    pane.append(messages);
    if (!store.ui.messagesAtBottom && store.ui.hasNewContent) {
      pane.append(renderNewContentButton());
    }
  }

  pane.append(renderApprovalDrawer(), renderComposer());
  return pane;
}

function renderEmptyState() {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  const title = document.createElement("strong");
  title.textContent = "开始一次技术文档研读";
  const copy = document.createElement("p");
  copy.textContent = "输入文档主题或学习目标，系统会生成计划并展示 agent 接力过程。";
  const starters = document.createElement("div");
  starters.className = "starter-grid";
  [
    "帮我读一下 LangGraph 的 StateGraph 文档并讲讲它",
    "总结 FastAPI 依赖注入的使用方式",
    "出几道题检查我是否理解 RAG 检索流程",
  ].forEach((prompt) => {
    const starter = document.createElement("button");
    starter.type = "button";
    starter.textContent = prompt;
    starter.addEventListener("click", () => sendMessage(prompt));
    starters.append(starter);
  });
  empty.append(title, copy, starters);
  return empty;
}

function renderMessage(message, index) {
  const group = document.createElement("article");
  const previous = store.messages[index - 1];
  const agent = normalizeAgent(message.agent);
  group.className = `message-group ${message.role || "assistant"}`;
  group.style.cssText = styleForAgent(agent);
  if (message.role === "assistant" && previous?.role === "assistant" && normalizeAgent(previous.agent) !== agent) {
    group.classList.add("agent-break");
  }

  const meta = document.createElement("div");
  meta.className = "message-meta";
  if (message.role === "assistant") {
    meta.append(agentBadge(agent));
    meta.append(document.createTextNode(message.streaming ? "生成中" : "agent message"));
  } else if (message.role === "user") {
    meta.textContent = "你";
  } else {
    meta.textContent = "system";
  }

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  const content = document.createElement("div");
  content.className = `message-content ${message.streaming ? "streaming-cursor" : ""}`;
  renderMarkdown(content, message.content || "");
  bubble.append(content);

  const toolIds = Array.isArray(message.toolCallIds) ? message.toolCallIds : [];
  if (toolIds.length) {
    const stack = document.createElement("div");
    stack.className = "tool-stack";
    toolIds.forEach((id) => {
      const tool = store.toolCalls[id];
      if (tool) stack.append(renderToolCallCard(tool));
    });
    bubble.append(stack);
  }

  group.append(meta, bubble);
  return group;
}

function renderToolCallCard(tool) {
  const details = document.createElement("details");
  details.className = "tool-card";
  details.style.cssText = styleForAgent(tool.agent);
  details.open = store.ui.expandedToolIds.has(tool.id);
  details.addEventListener("toggle", () => {
    if (details.open) store.ui.expandedToolIds.add(tool.id);
    else store.ui.expandedToolIds.delete(tool.id);
  });

  const summary = document.createElement("summary");
  const title = document.createElement("span");
  title.className = "tool-title";
  title.append(icon("tool", 18));
  const name = document.createElement("span");
  name.textContent = tool.tool || "tool";
  title.append(name);

  const badge = agentBadge(tool.agent);
  const status = document.createElement("span");
  status.className = `tool-status ${tool.status || "pending"}`;
  status.append(
    icon(tool.status === "done" ? "check" : tool.status === "error" ? "x" : "refresh", 13),
    document.createTextNode(tool.status === "done" ? "完成" : tool.status === "error" ? "错误" : "调用中"),
  );
  const chevron = document.createElement("span");
  chevron.className = "tool-chevron";
  chevron.textContent = "▶";
  summary.append(title, badge, status, chevron);

  const body = document.createElement("div");
  body.className = "tool-body";
  body.append(
    renderToolSection("args", pretty(tool.args)),
    renderToolSection("result", tool.result ? pretty(tool.result) : "等待工具返回..."),
  );

  details.append(summary, body);
  return details;
}

function renderToolSection(label, content) {
  const section = document.createElement("section");
  const header = document.createElement("div");
  header.className = "tool-section-header";
  const title = document.createElement("strong");
  title.textContent = label;
  const copy = button("tool-copy-button", "复制", "copy", async () => {
    try {
      await navigator.clipboard.writeText(content);
      showToast(`已复制 ${label}`);
    } catch {
      showToast("复制失败");
    }
  });
  header.append(title, copy);
  const pre = document.createElement("pre");
  pre.className = "json-block";
  pre.textContent = content;
  section.append(header, pre);
  return section;
}

function renderPlanStepper() {
  const wrapper = document.createElement("div");
  wrapper.className = "plan-stepper";
  const plan = Array.isArray(store.session.workflow_plan) ? store.session.workflow_plan : [];
  const index = Number(store.session.plan_index || 0);
  if (!plan.length) {
    const empty = document.createElement("div");
    empty.className = "empty-card";
    empty.textContent = "暂无计划";
    wrapper.append(empty);
    return wrapper;
  }
  plan.forEach((step, stepIndex) => {
    const agent = normalizeAgent(step);
    const item = document.createElement("button");
    item.type = "button";
    item.className = "plan-step";
    item.style.cssText = styleForAgent(agent);
    if (stepIndex < index) item.classList.add("done");
    if (stepIndex === index) item.classList.add("current");
    item.innerHTML = `
      <span class="step-node">${stepIndex < index ? "✓" : stepIndex + 1}</span>
      <strong>${escapeHtml(agentMeta(agent).label)}</strong>
      <small>${stepIndex < index ? "done" : stepIndex === index ? "active" : "queued"}</small>
    `;
    item.addEventListener("click", () => {
      const message = [...store.messages].reverse().find((entry) => entry.role === "assistant" && normalizeAgent(entry.agent) === agent);
      if (message) {
        showToast(`已定位 ${agent} 的最近消息`);
      }
    });
    wrapper.append(item);
  });
  return wrapper;
}

function renderApprovalDrawer() {
  const drawer = document.createElement("section");
  drawer.className = `approval-drawer ${store.session.pending_interrupt ? "" : "hidden"}`;
  const pending = lastPendingToolCall();
  const info = document.createElement("div");
  info.innerHTML = `
    <p class="approval-title">需要确认敏感操作</p>
    <p class="approval-copy">${escapeHtml(pending ? `${pending.agent} 请求执行 ${pending.tool}` : "后端正在等待你批准当前工具调用。")}</p>
  `;
  const textarea = document.createElement("textarea");
  textarea.placeholder = "拒绝时可填写反馈，例如：换一种检索范围";
  const actions = document.createElement("div");
  actions.className = "approval-actions";
  actions.append(
    button("danger-button", "拒绝", "x", () => sendApproval(false, textarea.value.trim()), { disabled: store.ui.running }),
    button("primary-button", "批准", "check", () => sendApproval(true), { disabled: store.ui.running }),
  );
  drawer.append(info, textarea, actions);
  return drawer;
}

function renderComposer() {
  const form = document.createElement("form");
  form.className = "composer";
  const textarea = document.createElement("textarea");
  textarea.rows = 1;
  textarea.placeholder = store.session.pending_interrupt ? "请先处理审批" : "输入文档链接、技术主题或你的问题...";
  textarea.disabled = store.ui.running || store.session.pending_interrupt;
  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  });
  textarea.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  const send = button("send-button", "", "send", null, { disabled: store.ui.running || store.session.pending_interrupt });
  send.type = "submit";
  form.append(textarea, send);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = textarea.value.trim();
    if (!text) return;
    textarea.value = "";
    sendMessage(text);
  });
  return form;
}

function renderLearner() {
  const layout = document.createElement("div");
  layout.className = "learner-layout";
  layout.append(renderLearnerHero());
  const grid = document.createElement("div");
  grid.className = "learner-grid";
  grid.append(renderKnowledgeRail(), renderLearnerCenter(), renderReviewRail());
  layout.append(grid);
  return layout;
}

function renderLearnerHero() {
  const hero = document.createElement("section");
  hero.className = "hero";
  const total = store.learning.total || 0;
  const needs = store.learning.needs_review_count || 0;
  const intro = document.createElement("div");
  intro.innerHTML = `
    <p class="eyebrow">Learning Overview</p>
    <h2 class="hero-title">${total ? `你已经掌握 ${total} 个知识点，${needs} 个建议复习` : "还没有学习记录，开始你的第一次研读吧"}</h2>
    <p class="hero-copy">学习台直接读取后端学习记录，点击知识卡可以启动复习对话。</p>
  `;
  const metrics = document.createElement("div");
  metrics.className = "hero-metrics";
  [
    ["总知识", String(total)],
    ["平均掌握度", `${Math.round((store.learning.average_score || 0) * 100)}%`],
    ["建议复习", String(needs)],
  ].forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "metric-card";
    card.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>`;
    metrics.append(card);
  });
  hero.append(intro, metrics);
  return hero;
}

function renderKnowledgeRail() {
  const rail = document.createElement("aside");
  rail.className = "knowledge-rail";
  rail.innerHTML = '<div class="panel-header"><h2 class="panel-title">我的知识库</h2></div>';
  const list = document.createElement("div");
  list.className = "knowledge-list";
  const records = store.learning.records || [];
  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "empty-card";
    empty.textContent = "暂无学习记录";
    list.append(empty);
  } else {
    records.forEach((record) => list.append(renderKnowledgeCard(record)));
  }
  rail.append(list);
  return rail;
}

function renderKnowledgeCard(record) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = `knowledge-card ${record.knowledge === store.session.learning_target ? "active" : ""}`;
  const score = Math.max(0, Math.min(1, Number(record.score || 0)));
  card.innerHTML = `
    <div class="knowledge-head">
      <strong>${escapeHtml(record.knowledge)}</strong>
      <span class="score-ring" style="--score:${score * 100};--score-color:${scoreTone(score)}">${Math.round(score * 100)}%</span>
    </div>
    <div class="knowledge-meta">
      <span>复习 ${Number(record.reviewtimes || 0)} 次</span>
      <span>${escapeHtml(relativeTime(record.timestamp))}</span>
    </div>
  `;
  card.addEventListener("click", () => sendMessage(`复习一下 ${record.knowledge}`));
  card.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    showToast(`${record.knowledge}：掌握度 ${Math.round(score * 100)}%，复习 ${record.reviewtimes || 0} 次`);
  });
  return card;
}

function renderLearnerCenter() {
  return renderChatPane({ mode: "learner" });
}

function reviewScore(record) {
  const score = Number(record.score || 0);
  const age = Math.min(daysSince(record.timestamp) / 30, 1);
  return (1 - score) * 0.6 + age * 0.4;
}

function renderReviewRail() {
  const rail = document.createElement("aside");
  rail.className = "review-rail";
  const header = document.createElement("div");
  header.className = "panel-header";
  header.innerHTML = '<h2 class="panel-title">复习队列</h2>';
  const start = button("primary-button", "开始复习", "play", () => {
    const targets = reviewRecords().slice(0, 3).map((record) => record.knowledge);
    sendMessage(targets.length ? `请围绕这些知识点出题检查我：${targets.join("、")}` : "请根据我的学习记录出几道复习题");
  });
  header.append(start);
  const list = document.createElement("div");
  list.className = "review-list";
  const records = reviewRecords();
  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "empty-card";
    empty.textContent = "暂无需要复习的知识点";
    list.append(empty);
  } else {
    records.forEach((record) => {
      const card = document.createElement("article");
      card.className = "review-card";
      card.innerHTML = `
        <strong>${escapeHtml(record.knowledge)}</strong>
        <span class="review-priority">优先级 ${Math.round(reviewScore(record) * 100)}</span>
        <p class="meta-line">掌握度 ${Math.round(Number(record.score || 0) * 100)}% · ${escapeHtml(relativeTime(record.timestamp))}</p>
      `;
      list.append(card);
    });
  }
  rail.append(header, list);
  return rail;
}

function reviewRecords() {
  return [...(store.learning.records || [])]
    .filter((record) => Number(record.score || 0) < 0.8 || daysSince(record.timestamp) > 14)
    .sort((a, b) => reviewScore(b) - reviewScore(a));
}

function renderQuizTakeover() {
  const shell = document.createElement("div");
  shell.className = "quiz-shell";
  const latestExam = [...store.messages].reverse().find((message) => message.role === "assistant" && normalizeAgent(message.agent) === "examination");
  const card = document.createElement("section");
  card.className = "quiz-card";
  card.innerHTML = '<p class="eyebrow">Examination Mode</p><h2 class="section-title">测验模式</h2>';
  const question = document.createElement("div");
  question.className = "message-content";
  renderMarkdown(question, latestExam?.content || "等待 examination agent 给出题目。");
  const answer = document.createElement("textarea");
  answer.className = "quiz-answer";
  answer.placeholder = "在这里作答，提交后会作为普通用户消息继续发送给 agent。";
  const submit = button("primary-button", "提交答案", "send", () => {
    const text = answer.value.trim();
    answer.value = "";
    sendMessage(text);
  }, { disabled: store.ui.running });
  card.append(question, answer, submit);
  shell.append(card);
  return shell;
}

function renderInspector() {
  const layout = document.createElement("div");
  layout.className = "inspector-layout";
  layout.append(renderInspectorToolbar(), renderSwimLane(), renderInspectorBottom());
  return layout;
}

function renderInspectorToolbar() {
  const toolbar = document.createElement("section");
  toolbar.className = "inspector-toolbar";
  const left = document.createElement("div");
  left.className = "toolbar-group";
  left.append(
    button(`chip ${store.ui.recording ? "active" : ""}`, store.ui.recording ? "录制中" : "已停止", store.ui.recording ? "play" : "pause", () => {
      store.ui.recording = !store.ui.recording;
      render();
    }),
    button(`chip ${store.ui.inspectorPaused ? "active" : ""}`, store.ui.inspectorPaused ? "继续渲染" : "暂停", store.ui.inspectorPaused ? "play" : "pause", () => {
      store.ui.inspectorPaused = !store.ui.inspectorPaused;
      render();
    }),
    button("chip", "回放", "refresh", replayEvents),
    button("chip", "导出 JSON", "download", exportTrace),
  );

  const filters = document.createElement("div");
  filters.className = "toolbar-group";
  EVENT_TYPES.forEach((type) => {
    const chip = button(`chip ${store.ui.filters.has(type) ? "active" : ""}`, type, "", () => {
      if (store.ui.filters.has(type)) store.ui.filters.delete(type);
      else store.ui.filters.add(type);
      render();
    });
    filters.append(chip);
  });
  toolbar.append(left, filters);
  return toolbar;
}

function renderSwimLane() {
  const lanes = document.createElement("section");
  lanes.className = "swim-lane";
  lanes.dataset.inspectorSwim = "true";
  const events = filteredEvents();
  const timelineBounds = getTimelineBounds(events);
  Object.keys(AGENTS).forEach((agent) => {
    const row = document.createElement("div");
    row.className = "lane-row";
    const label = document.createElement("div");
    label.className = "lane-label";
    label.append(agentBadge(agent));
    const track = document.createElement("div");
    track.className = "lane-track";
    events.forEach((event) => {
      if (normalizeAgent(event.agent) !== agent) return;
      const meta = event.data?.meta;
      if (event.type === "agent_message" && meta?.stream_started_at) {
        const segment = document.createElement("span");
        const start = positionForTime(meta.stream_started_at, timelineBounds);
        const end = positionForTime(event.timestamp, timelineBounds);
        segment.className = "lane-segment";
        segment.style.cssText = `${styleForAgent(agent)};--x:${start};--w:${Math.max(1.5, end - start)}`;
        segment.title = `${agent} stream · ${meta.streamed_token_count} tokens`;
        track.append(segment);
      }
      const marker = document.createElement("button");
      marker.type = "button";
      marker.className = `lane-marker ${laneMarkerClass(event)} ${event.id === store.ui.selectedEventId || event.id === store.ui.replayingEventId ? "selected" : ""}`;
      marker.style.cssText = `${styleForAgent(agent)};--x:${positionForTime(event.timestamp, timelineBounds)}`;
      marker.title = `${event.type}: ${eventSummary(event)}`;
      marker.addEventListener("click", () => {
        store.ui.selectedEventId = event.id;
        renderInspectorFrame({ forceDetail: true });
      });
      track.append(marker);
    });
    if (store.ui.running && normalizeAgent(store.session.current_agent) === agent) {
      const live = document.createElement("span");
      live.className = "lane-live";
      live.style.cssText = styleForAgent(agent);
      track.append(live);
    }
    row.append(label, track);
    lanes.append(row);
  });
  return lanes;
}

function getTimelineBounds(events) {
  const times = [];
  events.forEach((event) => {
    const time = new Date(event.timestamp).getTime();
    if (Number.isFinite(time)) times.push(time);
    const start = new Date(event.data?.meta?.stream_started_at || "").getTime();
    if (Number.isFinite(start)) times.push(start);
  });
  if (!times.length) {
    const now = Date.now();
    return { min: now, max: now + 1 };
  }
  const min = Math.min(...times);
  const max = Math.max(...times);
  return { min, max: max === min ? min + 1 : max };
}

function positionForTime(timestamp, bounds) {
  const time = new Date(timestamp).getTime();
  if (!Number.isFinite(time)) return 0;
  return Math.max(0, Math.min(100, ((time - bounds.min) / (bounds.max - bounds.min)) * 100));
}

function laneMarkerClass(event) {
  if (event.type === "agent_transition") {
    const phase = event.data?.phase || "enter";
    return `agent_transition transition-${phase}`;
  }
  if (event.type === "tool_result" && /error|exception|traceback/i.test(String(event.data?.content || ""))) {
    return "tool_result tool_result_error";
  }
  return event.type;
}

function renderInspectorBottom() {
  const bottom = document.createElement("section");
  bottom.className = "inspector-bottom";
  const pane = document.createElement("div");
  pane.className = "inspector-pane";
  pane.innerHTML = '<div class="panel-header"><h2 class="panel-title">事件列表</h2></div>';
  const list = document.createElement("div");
  list.className = "event-list";
  list.dataset.inspectorEvents = "true";
  renderEventListInto(list);
  pane.append(list);
  bottom.append(pane, renderEventDetail());
  return bottom;
}

function renderEventListInto(list) {
  const top = list.scrollTop;
  list.replaceChildren();
  const events = filteredEvents();
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty-card";
    empty.textContent = "暂无事件";
    list.append(empty);
  } else {
    events.forEach((event) => list.append(renderEventRow(event)));
  }
  list.scrollTop = top;
}

function renderEventRow(event) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = `event-row ${event.id === store.ui.selectedEventId ? "selected" : ""}`;
  row.style.cssText = styleForAgent(event.agent);
  row.innerHTML = `
    <code>${escapeHtml(formatTime(event.timestamp))}</code>
    <span>${escapeHtml(normalizeAgent(event.agent))}</span>
    <span class="event-summary"><span class="event-type" style="background:color-mix(in srgb, var(--agent-color) 18%, transparent);color:var(--agent-color)">${escapeHtml(event.type)}</span> ${escapeHtml(eventSummary(event))}</span>
  `;
  row.addEventListener("click", () => {
    store.ui.selectedEventId = event.id;
    renderInspectorFrame({ forceDetail: true });
  });
  return row;
}

function renderEventDetail() {
  const pane = document.createElement("div");
  pane.className = "detail-pane";
  pane.dataset.inspectorDetail = "true";
  renderEventDetailInto(pane);
  return pane;
}

function renderEventDetailInto(pane) {
  const top = pane.scrollTop;
  pane.replaceChildren();
  const header = document.createElement("div");
  header.className = "panel-header";
  header.innerHTML = '<h2 class="panel-title">事件详情</h2>';
  pane.append(header);
  const visibleEvents = filteredEvents();
  const event = visibleEvents.find((item) => item.id === store.ui.selectedEventId) || visibleEvents.at(-1);
  pane.dataset.eventId = event?.id || "";
  if (!event) {
    const empty = document.createElement("div");
    empty.className = "detail-empty";
    empty.textContent = "选择一个事件查看 payload";
    pane.append(empty);
    pane.scrollTop = top;
    return;
  }
  const actions = document.createElement("div");
  actions.className = "toolbar-group";
  actions.append(
    button("text-button", "复制 JSON", "copy", async () => {
      await navigator.clipboard.writeText(JSON.stringify(event, null, 2));
      showToast("已复制事件 JSON");
    }),
    button("text-button", "在 Studio 看", "layers", () => navigate("studio")),
  );
  const pre = document.createElement("pre");
  pre.className = "json-block";
  pre.textContent = JSON.stringify(event, null, 2);
  pane.append(actions, pre);
  pane.scrollTop = top;
}

function exportTrace() {
  const payload = {
    session: store.session,
    events: store.events,
    messages: store.messages,
    toolCalls: store.toolCalls,
    exportedAt: new Date().toISOString(),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `trace_${store.session.id}_${Date.now()}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function replayEvents() {
  const events = filteredEvents();
  if (!events.length) return;
  let index = 0;
  const tick = () => {
    store.ui.replayingEventId = events[index].id;
    store.ui.selectedEventId = events[index].id;
    render();
    index += 1;
    if (index < events.length) window.setTimeout(tick, 160);
    else {
      window.setTimeout(() => {
        store.ui.replayingEventId = null;
        render();
      }, 300);
    }
  };
  tick();
}

function bindGlobalKeys() {
  window.addEventListener("keydown", (event) => {
    if (!(event.metaKey || event.ctrlKey)) return;
    if (event.key === "1") {
      event.preventDefault();
      navigate("studio");
    }
    if (event.key === "2") {
      event.preventDefault();
      navigate("inspector");
    }
    if (event.key === "3") {
      event.preventDefault();
      navigate("learner");
    }
  });
  window.addEventListener("popstate", () => {
    store.ui.view = currentPathView();
    const params = new URLSearchParams(window.location.search);
    const sessionId = params.get("session");
    if (sessionId && sessionId !== store.session.id) {
      setSessionId(sessionId, { keepUrl: true });
      loadSession();
    }
    render();
  });
}

async function init() {
  store.ui.theme = localStorage.getItem(storage.theme) || "dark";
  setTheme(store.ui.theme);
  store.ui.view = currentPathView();
  store.sessions = readSessions();
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session") || localStorage.getItem(storage.session) || makeSessionId();
  setSessionId(sessionId, { keepUrl: true });
  syncUrl();
  bindGlobalKeys();
  render();
  await Promise.all([loadSession(), loadLearning()]);
}

init();
