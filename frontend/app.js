const els = {
  sessionInput: document.querySelector("#sessionInput"),
  newSessionButton: document.querySelector("#newSessionButton"),
  copySessionButton: document.querySelector("#copySessionButton"),
  reloadHistoryButton: document.querySelector("#reloadHistoryButton"),
  topReloadButton: document.querySelector("#topReloadButton"),
  includeToolsToggle: document.querySelector("#includeToolsToggle"),
  currentAgent: document.querySelector("#currentAgent"),
  messageCount: document.querySelector("#messageCount"),
  learningTarget: document.querySelector("#learningTarget"),
  pendingState: document.querySelector("#pendingState"),
  statusDot: document.querySelector("#statusDot"),
  planList: document.querySelector("#planList"),
  planProgress: document.querySelector("#planProgress"),
  workspaceTitle: document.querySelector("#workspaceTitle"),
  runState: document.querySelector("#runState"),
  messages: document.querySelector("#messages"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  approvalBox: document.querySelector("#approvalBox"),
  approvalFeedback: document.querySelector("#approvalFeedback"),
  approveButton: document.querySelector("#approveButton"),
  rejectButton: document.querySelector("#rejectButton"),
  activityList: document.querySelector("#activityList"),
  clearActivityButton: document.querySelector("#clearActivityButton"),
};

const storage = {
  session: "tech-doc-agent.session",
  includeTools: "tech-doc-agent.include-tools",
  transcriptPrefix: "tech-doc-agent.transcript.",
  transcriptVersion: 5,
};

const agentNames = {
  primary: "primary",
  primary_assistant: "primary",
  parser: "parser",
  relation: "relation",
  explanation: "explanation",
  examination: "examination",
  summary: "summary",
};

let messages = [];
let activities = [];
let busy = false;
let statePollTimer = null;
let persistTimer = null;
let currentWorkflowPlan = [];

function uid() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

function makeSessionId() {
  const date = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  const random = Math.random().toString(16).slice(2, 8);
  return `doc-${date}-${random}`;
}

function getSessionId() {
  const value = els.sessionInput.value.trim();
  if (value) return value;
  const next = makeSessionId();
  setSessionId(next);
  return next;
}

function setSessionId(sessionId) {
  els.sessionInput.value = sessionId;
  localStorage.setItem(storage.session, sessionId);
  els.workspaceTitle.textContent = sessionId;
}

function transcriptKey(sessionId = getSessionId()) {
  return `${storage.transcriptPrefix}${sessionId}`;
}

function normalizeAgentName(agent) {
  if (!agent) return "primary";
  const normalized = String(agent).trim();
  if (normalized === "primary_assistant") return "primary";

  for (const name of ["parser", "relation", "explanation", "examination", "summary"]) {
    if (normalized === name || normalized.startsWith(`${name}_assistant`)) {
      return name;
    }
  }

  return normalized;
}

function finalAgentFromPlan(plan) {
  if (!Array.isArray(plan) || plan.length === 0) return "primary";
  return normalizeAgentName(plan[plan.length - 1]);
}

function latestAssistantAgent(items, fallback = "primary") {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.role === "assistant" && String(item.content || "").trim()) {
      return normalizeAgentName(item.agent || item.name || fallback);
    }
  }
  return normalizeAgentName(fallback);
}

function persistMessagesNow() {
  window.clearTimeout(persistTimer);
  if (messages.length === 0) {
    localStorage.removeItem(transcriptKey());
    return;
  }

  const transcript = messages.map((message) => ({
    id: message.id,
    role: message.role,
    kind: message.kind,
    content: message.content,
    agent: message.agent,
    name: message.name,
    responseId: message.responseId,
    isFinal: message.isFinal,
    collapsible: message.collapsible,
    expanded: message.expanded,
  }));

  localStorage.setItem(
    transcriptKey(),
    JSON.stringify({
      version: storage.transcriptVersion,
      savedAt: new Date().toISOString(),
      messages: transcript,
    }),
  );
}

function schedulePersistMessages() {
  window.clearTimeout(persistTimer);
  persistTimer = window.setTimeout(persistMessagesNow, 160);
}

function readCachedMessages(sessionId) {
  try {
    const raw = localStorage.getItem(transcriptKey(sessionId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (parsed.version !== storage.transcriptVersion) return [];
    if (!Array.isArray(parsed.messages)) return [];
    return parsed.messages.map((message) => ({
      id: message.id || uid(),
      role: message.role || "assistant",
      kind: message.kind || "message",
      content: message.content || "",
      agent: normalizeAgentName(message.agent || message.name || "primary"),
      name: message.name || "",
      responseId: message.responseId || "",
      isFinal: message.isFinal !== false,
      collapsible: Boolean(message.collapsible),
      expanded: Boolean(message.expanded),
      streaming: false,
    }));
  } catch (error) {
    return [];
  }
}

function setBusy(next, label = "处理中") {
  busy = next;
  els.messageInput.disabled = next;
  els.sendButton.disabled = next;
  els.approveButton.disabled = next;
  els.rejectButton.disabled = next;
  els.runState.classList.toggle("busy", next);
  els.statusDot.classList.toggle("busy", next);
  if (next) {
    els.runState.textContent = label;
    els.runState.classList.remove("error");
    els.statusDot.classList.remove("error");
  } else if (!els.runState.classList.contains("error")) {
    els.runState.textContent = "就绪";
  }
}

function setErrorState(message) {
  els.runState.textContent = "错误";
  els.runState.classList.add("error");
  els.runState.classList.remove("busy");
  els.statusDot.classList.add("error");
  els.statusDot.classList.remove("busy");
  addMessage({
    role: "system",
    kind: "error",
    content: message,
  });
}

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  document.body.append(toast);
  window.setTimeout(() => toast.remove(), 1800);
}

function scrollMessagesToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function autoSizeTextarea(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
}

function roleLabel(message) {
  if (message.role === "user") return "你";
  if (message.role === "assistant") {
    const agent = normalizeAgentName(message.agent || message.name || "primary");
    const label = agentNames[agent] || agent || "assistant";
    return message.isFinal === false ? `中间过程 · ${label}` : `最终回复 · ${label}`;
  }
  if (message.role === "tool") return message.name || "tool";
  return "system";
}

function addMessage(message) {
  const normalized = {
    id: message.id || uid(),
    role: message.role || "assistant",
    kind: message.kind || "message",
    content: message.content || "",
    agent: normalizeAgentName(message.agent || message.name || "primary"),
    name: message.name || "",
    responseId: message.responseId || "",
    isFinal: message.isFinal !== false,
    collapsible: Boolean(message.collapsible),
    expanded: Boolean(message.expanded),
    streaming: Boolean(message.streaming),
  };
  messages.push(normalized);
  renderMessages();
  schedulePersistMessages();
  return normalized;
}

function renderMessages() {
  els.messages.replaceChildren();

  if (messages.length === 0) {
    renderEmptyState();
    return;
  }

  for (const message of messages) {
    if (message.role === "assistant" && message.collapsible && message.isFinal === false) {
      renderCollapsedMessage(message);
      continue;
    }

    const item = document.createElement("article");
    item.className = `message ${message.role}`;
    if (message.role === "assistant" && message.isFinal !== false) {
      item.classList.add("final-agent");
    }

    const header = document.createElement("div");
    header.className = "message-header";
    header.textContent = roleLabel(message);

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    if (message.streaming) bubble.classList.add("typing-cursor");
    bubble.textContent = message.content || (message.streaming ? "" : " ");

    item.append(header, bubble);
    els.messages.append(item);
  }

  scrollMessagesToBottom();
}

function renderCollapsedMessage(message) {
  const details = document.createElement("details");
  details.className = "message assistant trace-message";
  details.open = Boolean(message.expanded);

  const summary = document.createElement("summary");
  summary.className = "trace-summary";

  const label = document.createElement("span");
  label.textContent = roleLabel(message);

  const meta = document.createElement("span");
  meta.className = "trace-meta";
  meta.textContent = message.streaming ? "生成中" : "已折叠";

  summary.append(label, meta);

  const bubble = document.createElement("div");
  bubble.className = "message-bubble trace-bubble";
  if (message.streaming) bubble.classList.add("typing-cursor");
  bubble.textContent = message.content || (message.streaming ? "" : " ");

  details.addEventListener("toggle", () => {
    message.expanded = details.open;
    schedulePersistMessages();
  });

  details.append(summary, bubble);
  els.messages.append(details);
}

function renderEmptyState() {
  const wrapper = document.createElement("div");
  wrapper.className = "empty-state";

  const title = document.createElement("strong");
  title.textContent = "开始一次技术文档研读";

  const copy = document.createElement("p");
  copy.textContent = "可以直接贴入文档片段、链接，或输入一个想理解的技术主题。";

  const starters = document.createElement("div");
  starters.className = "starter-grid";

  const prompts = [
    "帮我研读 LangGraph StateGraph 的核心概念",
    "总结 FastAPI 依赖注入的使用方式",
    "出几道题检查我是否理解 RAG 检索流程",
  ];

  for (const prompt of prompts) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = prompt;
    button.addEventListener("click", () => {
      els.messageInput.value = prompt;
      autoSizeTextarea(els.messageInput);
      els.messageInput.focus();
    });
    starters.append(button);
  }

  wrapper.append(title, copy, starters);
  els.messages.append(wrapper);
}

function renderState(state = {}) {
  const currentAgent = state.current_agent || "primary";
  const plan = Array.isArray(state.workflow_plan) ? state.workflow_plan : [];
  const planIndex = Number.isInteger(state.plan_index) ? state.plan_index : 0;
  const pending = Boolean(state.pending_interrupt);
  currentWorkflowPlan = plan;

  els.currentAgent.textContent = normalizeAgentName(currentAgent);
  els.messageCount.textContent = String(state.message_count || 0);
  els.learningTarget.textContent = state.learning_target || "-";
  els.pendingState.textContent = pending ? "待确认" : "无";
  els.approvalBox.classList.toggle("hidden", !pending);
  els.planProgress.textContent = `${Math.min(planIndex, plan.length)}/${plan.length}`;
  els.planList.replaceChildren();

  if (plan.length === 0) {
    const item = document.createElement("li");
    item.textContent = "暂无计划";
    els.planList.append(item);
  } else {
    plan.forEach((step, index) => {
      const item = document.createElement("li");
      item.textContent = normalizeAgentName(step);
      if (index < planIndex) item.classList.add("done");
      if (index === planIndex) item.classList.add("current");
      els.planList.append(item);
    });
  }
}

function addActivity(kind, payload) {
  const item = {
    id: uid(),
    kind,
    payload: payload || {},
    time: new Date(),
  };
  activities.unshift(item);
  activities = activities.slice(0, 40);
  renderActivities();
}

function renderActivities() {
  els.activityList.replaceChildren();

  if (activities.length === 0) {
    const empty = document.createElement("div");
    empty.className = "activity-item";
    empty.textContent = "暂无工具活动";
    els.activityList.append(empty);
    return;
  }

  for (const activity of activities) {
    const payload = activity.payload;
    const item = document.createElement("article");
    item.className = "activity-item";

    const title = document.createElement("div");
    title.className = "activity-title";
    title.innerHTML = '<svg aria-hidden="true"><use href="#icon-tool"></use></svg>';
    title.append(document.createTextNode(activity.kind === "tool_call" ? payload.tool || "tool call" : payload.tool || "tool result"));

    const meta = document.createElement("div");
    meta.className = "activity-meta";
    meta.textContent = `${normalizeAgentName(payload.agent || "unknown")} · ${activity.time.toLocaleTimeString()}`;

    item.append(title, meta);

    const detail = activity.kind === "tool_call" ? payload.args : payload.content;
    const content = document.createElement("div");
    content.className = "activity-content";
    if (typeof detail === "string") {
      content.textContent = detail || "(empty string)";
    } else if (detail === undefined || detail === null) {
      content.textContent = "(empty)";
    } else {
      content.textContent = JSON.stringify(detail, null, 2);
    }
    item.append(content);

    els.activityList.append(item);
  }
}

async function fetchJson(path) {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function loadState() {
  const sessionId = getSessionId();
  try {
    const state = await fetchJson(`/sessions/${encodeURIComponent(sessionId)}/state`);
    renderState(state);
  } catch (error) {
    renderState({});
  }
}

async function loadHistory() {
  const sessionId = getSessionId();
  const includeTools = els.includeToolsToggle.checked ? "true" : "false";
  try {
    const [history, state] = await Promise.all([
      fetchJson(`/sessions/${encodeURIComponent(sessionId)}/history?include_tools=${includeTools}`),
      fetchJson(`/sessions/${encodeURIComponent(sessionId)}/state`),
    ]);
    const cachedMessages = includeTools === "false" ? readCachedMessages(sessionId) : [];
    const historyItems = history.messages || [];
    const plannedFinalAgent = finalAgentFromPlan(state.workflow_plan);
    const historyAgents = historyItems
      .filter((item) => item.role === "assistant")
      .map((item) => normalizeAgentName(item.agent || item.name || "primary"));
    const fallbackFinalAgent = historyAgents.includes(plannedFinalAgent)
      ? plannedFinalAgent
      : latestAssistantAgent(historyItems, plannedFinalAgent);
    messages = cachedMessages.length > 0
      ? cachedMessages
      : historyItems.map((item) => ({
        id: item.id || uid(),
        role: item.role,
        kind: item.kind,
        content: item.content || "",
        agent: normalizeAgentName(item.agent || item.name || "primary"),
        name: item.name || "",
        isFinal: item.role !== "assistant" || normalizeAgentName(item.agent || item.name || "primary") === fallbackFinalAgent,
        collapsible: item.role === "assistant" && normalizeAgentName(item.agent || item.name || "primary") !== fallbackFinalAgent,
      }));
    renderMessages();
    renderState({ ...history, ...state });
  } catch (error) {
    messages = [];
    renderMessages();
    renderState({});
  }
}

function parsePayload(raw) {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed === "string" && /^[\[{]/.test(parsed.trim())) {
      return JSON.parse(parsed);
    }
    if (parsed && typeof parsed === "object") {
      return parsed;
    }
    return { value: parsed, text: String(parsed), message: String(parsed) };
  } catch (error) {
    return { raw, text: raw, message: raw };
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

  return {
    event,
    data: parsePayload(dataLines.join("\n")),
  };
}

function createResponseContext(options = {}) {
  return {
    id: uid(),
    finalAgent: options.useCurrentPlan ? finalAgentFromPlan(currentWorkflowPlan) : "primary",
    segments: new Map(),
    completedMessages: [],
    completedKeys: new Set(),
  };
}

function markResponseSegments(context) {
  for (const message of messages) {
    if (message.responseId !== context.id || message.role !== "assistant") continue;
    message.agent = normalizeAgentName(message.agent);
    message.isFinal = message.agent === context.finalAgent;
    message.collapsible = !message.isFinal;
  }
}

function getResponseSegment(context, rawAgent) {
  const agent = normalizeAgentName(rawAgent || context.finalAgent);
  const existing = context.segments.get(agent);
  if (existing) return existing;

  const segment = addMessage({
    role: "assistant",
    content: "",
    agent,
    responseId: context.id,
    isFinal: agent === context.finalAgent,
    collapsible: agent !== context.finalAgent,
    streaming: true,
  });
  context.segments.set(agent, segment);
  markResponseSegments(context);
  return segment;
}

function updateResponsePlanFromToolCall(context, data) {
  if (data.tool !== "PlanWorkflow") return;
  const steps = data.args && Array.isArray(data.args.steps) ? data.args.steps : [];
  if (steps.length === 0) return;
  currentWorkflowPlan = steps.map(normalizeAgentName);
  context.finalAgent = finalAgentFromPlan(currentWorkflowPlan);
  markResponseSegments(context);
  renderState({
    current_agent: "primary",
    workflow_plan: currentWorkflowPlan,
    plan_index: 0,
    pending_interrupt: false,
    message_count: messages.length,
    learning_target: els.learningTarget.textContent === "-" ? null : els.learningTarget.textContent,
  });
  renderMessages();
  schedulePersistMessages();
}

function addCompletedAgentMessage(context, data) {
  const agent = normalizeAgentName(data.agent || context.finalAgent);
  const content = data.content || "";
  if (!content.trim()) return;

  const key = data.message_id || `${agent}:${content}`;
  if (context.completedKeys.has(key)) return;
  context.completedKeys.add(key);
  context.completedMessages.push({
    id: data.message_id || uid(),
    role: "assistant",
    kind: "message",
    content,
    agent,
    responseId: context.id,
  });
  context.finalAgent = agent;

  const segment = getResponseSegment(context, agent);
  segment.content = content;
  segment.streaming = false;
  markResponseSegments(context);
  renderMessages();
  schedulePersistMessages();
}

function finalizeResponseContext(context) {
  for (const segment of context.segments.values()) {
    segment.streaming = false;
  }
  if (context.completedMessages.length > 0) {
    context.finalAgent = latestAssistantAgent(context.completedMessages, context.finalAgent);

    const firstIndex = messages.findIndex(
      (message) => message.responseId === context.id && message.role === "assistant",
    );
    const rebuilt = context.completedMessages.map((message) => ({
      ...message,
      agent: normalizeAgentName(message.agent),
      isFinal: normalizeAgentName(message.agent) === context.finalAgent,
      collapsible: normalizeAgentName(message.agent) !== context.finalAgent,
      expanded: false,
      streaming: false,
    }));
    messages = messages.filter(
      (message) => message.responseId !== context.id || message.role !== "assistant",
    );
    if (firstIndex === -1 || firstIndex >= messages.length) {
      messages.push(...rebuilt);
    } else {
      messages.splice(firstIndex, 0, ...rebuilt);
    }
  } else {
    const responseMessages = messages.filter((message) => (
      message.responseId === context.id
      && message.role === "assistant"
      && message.content.trim()
    ));
    context.finalAgent = latestAssistantAgent(responseMessages, context.finalAgent);
    markResponseSegments(context);
    messages = messages.filter((message) => {
      if (message.responseId !== context.id || message.role !== "assistant") return true;
      return message.content.trim();
    });
  }
  renderMessages();
  persistMessagesNow();
}

async function postSse(path, body, onEvent) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }

  if (!response.body) {
    throw new Error("浏览器没有返回可读取的响应流。");
  }

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

function handleStreamEvent(event, responseContext) {
  const { event: type, data } = event;

  if (type === "token") {
    const segment = getResponseSegment(responseContext, data.agent);
    segment.content += data.text || "";
    segment.agent = normalizeAgentName(data.agent || segment.agent);
    segment.streaming = true;
    markResponseSegments(responseContext);
    renderMessages();
    schedulePersistMessages();
    return;
  }

  if (type === "tool_call" || type === "tool_result") {
    if (type === "tool_call") {
      updateResponsePlanFromToolCall(responseContext, data);
    }
    addActivity(type, data);
    if (data.agent) {
      els.currentAgent.textContent = normalizeAgentName(data.agent);
    }
    return;
  }

  if (type === "agent_message") {
    addCompletedAgentMessage(responseContext, data);
    if (data.agent) {
      els.currentAgent.textContent = normalizeAgentName(data.agent);
    }
    return;
  }

  if (type === "interrupt_required") {
    els.approvalBox.classList.remove("hidden");
    els.pendingState.textContent = "待确认";
    return;
  }

  if (type === "no_pending_interrupt") {
    showToast("当前没有等待确认的操作");
    return;
  }

  if (type === "done") {
    els.approvalBox.classList.add("hidden");
    return;
  }

  if (type === "error") {
    throw new Error(data.message || "后端返回错误事件。");
  }
}

async function sendMessage(text) {
  const sessionId = getSessionId();
  addMessage({ role: "user", content: text });
  const responseContext = createResponseContext();

  setBusy(true, "生成中");
  try {
    await postSse("/chat", { session_id: sessionId, message: text }, async (event) => {
      handleStreamEvent(event, responseContext);
    });
    finalizeResponseContext(responseContext);
    await loadState();
  } catch (error) {
    finalizeResponseContext(responseContext);
    setErrorState(error.message || String(error));
  } finally {
    setBusy(false);
    els.messageInput.focus();
  }
}

async function sendApproval(approved) {
  const sessionId = getSessionId();
  const feedback = approved ? "" : els.approvalFeedback.value.trim();
  const responseContext = createResponseContext({ useCurrentPlan: true });

  setBusy(true, approved ? "继续执行" : "提交反馈");
  try {
    await postSse(
      "/chat/approve",
      { session_id: sessionId, approved, feedback },
      async (event) => handleStreamEvent(event, responseContext),
    );
    finalizeResponseContext(responseContext);
    els.approvalFeedback.value = "";
    await loadState();
  } catch (error) {
    finalizeResponseContext(responseContext);
    setErrorState(error.message || String(error));
  } finally {
    setBusy(false);
  }
}

function startStatePolling() {
  window.clearInterval(statePollTimer);
  statePollTimer = window.setInterval(() => {
    if (!busy) loadState();
  }, 12000);
}

function bindEvents() {
  els.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = els.messageInput.value.trim();
    if (!text || busy) return;
    els.messageInput.value = "";
    autoSizeTextarea(els.messageInput);
    sendMessage(text);
  });

  els.messageInput.addEventListener("input", () => autoSizeTextarea(els.messageInput));
  els.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      els.composer.requestSubmit();
    }
  });

  els.newSessionButton.addEventListener("click", () => {
    setSessionId(makeSessionId());
    messages = [];
    activities = [];
    renderMessages();
    renderActivities();
    renderState({});
    persistMessagesNow();
    els.messageInput.focus();
  });

  els.copySessionButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(getSessionId());
      showToast("已复制会话 ID");
    } catch (error) {
      showToast("复制失败");
    }
  });

  els.reloadHistoryButton.addEventListener("click", loadHistory);
  els.topReloadButton.addEventListener("click", loadHistory);

  els.includeToolsToggle.addEventListener("change", () => {
    localStorage.setItem(storage.includeTools, String(els.includeToolsToggle.checked));
    loadHistory();
  });

  els.sessionInput.addEventListener("change", () => {
    const sessionId = getSessionId();
    setSessionId(sessionId);
    loadHistory();
  });

  els.approveButton.addEventListener("click", () => sendApproval(true));
  els.rejectButton.addEventListener("click", () => sendApproval(false));

  els.clearActivityButton.addEventListener("click", () => {
    activities = [];
    renderActivities();
  });
}

function init() {
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session") || localStorage.getItem(storage.session) || makeSessionId();
  setSessionId(sessionId);
  els.includeToolsToggle.checked = localStorage.getItem(storage.includeTools) === "true";
  bindEvents();
  renderMessages();
  renderActivities();
  loadHistory();
  startStatePolling();
}

init();
