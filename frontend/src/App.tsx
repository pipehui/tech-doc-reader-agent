import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  BookOpen,
  Check,
  Copy,
  Download,
  Layers,
  Moon,
  Pause,
  Play,
  Plus,
  RefreshCcw,
  Send,
  Sun,
  ToolCase,
  X
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { agentMeta, agentStyle, normalizeAgent } from "./agentColors";
import { getLearningOverview, getSessionHistory, getSessionState } from "./api";
import { EVENT_TYPES, useAppStore } from "./store";
import { useChatStream } from "./useChatStream";
import type { CSSProperties } from "react";
import type { AgentKey, ChatMessage, LearningRecord, SessionState, ToolCall, TraceEvent } from "./types";
import { daysSince, formatTime, makeSessionId, pretty, relativeTime, scoreTone, uid } from "./utils";

function isAtBottom(el: HTMLElement) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

function routeName(pathname: string) {
  const name = pathname.replace(/^\/+/, "").split("/")[0];
  return ["studio", "inspector", "learner"].includes(name) ? name : "studio";
}

function toMessages(history: Awaited<ReturnType<typeof getSessionHistory>>, state: SessionState): ChatMessage[] {
  return (history.messages || []).map((item) => ({
    id: item.id || uid(),
    role: item.role,
    agent: normalizeAgent(item.name || state.current_agent || "primary"),
    content: item.content || "",
    streaming: false,
    toolCallIds: [],
    createdAt: new Date().toISOString()
  }));
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const sessionId = useAppStore((state) => state.session.session_id);
  const theme = useAppStore((state) => state.theme);
  const setSessionId = useAppStore((state) => state.setSessionId);
  const hydrateTranscript = useAppStore((state) => state.hydrateTranscript);
  const setSessionState = useAppStore((state) => state.setSessionState);
  const setMessages = useAppStore((state) => state.setMessages);
  const setLearning = useAppStore((state) => state.setLearning);
  const addSystemMessage = useAppStore((state) => state.addSystemMessage);
  const rememberSession = useAppStore((state) => state.rememberSession);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const nextSession = params.get("session") || sessionId || makeSessionId();
    if (nextSession !== sessionId) setSessionId(nextSession);
    else rememberSession(nextSession);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("session") !== sessionId) {
      params.set("session", sessionId);
      navigate(`${location.pathname}?${params.toString()}`, { replace: true });
    }
  }, [sessionId, location.pathname]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const cached = hydrateTranscript(sessionId);
      try {
        const [state, history, learning] = await Promise.all([
          getSessionState(sessionId),
          getSessionHistory(sessionId),
          getLearningOverview()
        ]);
        if (cancelled) return;
        setSessionState(state);
        if (!cached) setMessages(toMessages(history, state));
        setLearning(learning);
      } catch (error) {
        if (!cancelled) addSystemMessage(`会话恢复失败：${error instanceof Error ? error.message : String(error)}`);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  return (
    <div className="app-shell">
      <Topbar view={routeName(location.pathname)} />
      <main className="view-frame">
        <Routes>
          <Route path="/" element={<Navigate to={`/studio?session=${encodeURIComponent(sessionId)}`} replace />} />
          <Route path="/studio" element={<Studio />} />
          <Route path="/inspector" element={<Inspector />} />
          <Route path="/learner" element={<Learner />} />
          <Route path="*" element={<Navigate to={`/studio?session=${encodeURIComponent(sessionId)}`} replace />} />
        </Routes>
      </main>
      <ToastHost />
    </div>
  );
}

function Topbar({ view }: { view: string }) {
  const navigate = useNavigate();
  const session = useAppStore((state) => state.session);
  const running = useAppStore((state) => state.running);
  const runLabel = useAppStore((state) => state.runLabel);
  const error = useAppStore((state) => state.error);
  const theme = useAppStore((state) => state.theme);
  const setTheme = useAppStore((state) => state.setTheme);
  const resetForSession = useAppStore((state) => state.resetForSession);
  const newSession = useAppStore((state) => state.newSession);
  const [draft, setDraft] = useState(session.session_id);

  useEffect(() => setDraft(session.session_id), [session.session_id]);

  function go(next: string) {
    navigate(`/${next}?session=${encodeURIComponent(session.session_id)}`);
  }

  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">TD</div>
        <div>
          <p className="eyebrow">LangGraph Agent</p>
          <h1 className="brand-title">技术文档研读助手</h1>
        </div>
      </div>

      <nav className="view-switcher">
        {([
          ["studio", "Studio", Layers],
          ["inspector", "Inspector", Activity],
          ["learner", "Learner", BookOpen]
        ] as const).map(([key, label, Icon]) => (
          <button key={String(key)} className={`view-tab ${view === key ? "active" : ""}`} type="button" onClick={() => go(String(key))}>
            <Icon size={16} />
            {label}
          </button>
        ))}
      </nav>

      <div className="topbar-actions">
        <label className="session-control">
          <span>Session</span>
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => draft.trim() && resetForSession(draft.trim())}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
          />
        </label>
        <button className="icon-button" type="button" title="复制 session id" onClick={() => navigator.clipboard.writeText(session.session_id)}>
          <Copy size={16} />
        </button>
        <button className="icon-button" type="button" title="新建会话" onClick={newSession}>
          <Plus size={16} />
        </button>
        <button className="icon-button" type="button" title="切换主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
        <span className={`status-pill ${error ? "error" : running ? "running" : ""}`}>{error ? "错误" : runLabel}</span>
      </div>
    </header>
  );
}

function Studio() {
  return (
    <div className="studio-grid">
      <StudioRail />
      <ChatPane mode="studio" />
      <Observer />
    </div>
  );
}

function StudioRail() {
  const session = useAppStore((state) => state.session);
  const sessions = useAppStore((state) => state.sessions);
  const messages = useAppStore((state) => state.messages);
  const resetForSession = useAppStore((state) => state.resetForSession);
  const entries = sessions.length ? sessions : [{ id: session.session_id, updatedAt: new Date().toISOString() }];
  return (
    <aside className="rail">
      <section className="panel">
        <div className="panel-header"><h2 className="panel-title">当前会话</h2></div>
        <dl className="state-grid">
          <StateCell label="Agent" value={normalizeAgent(session.current_agent)} />
          <StateCell label="消息" value={String(session.message_count || messages.length)} />
          <StateCell label="目标" value={session.learning_target || "-"} />
          <StateCell label="审批" value={session.pending_interrupt ? "待确认" : "无"} />
        </dl>
      </section>
      <section className="panel session-list">
        <div className="panel-header"><h2 className="panel-title">会话列表</h2></div>
        <div className="session-list">
          {entries.map((item) => (
            <button key={item.id} className={`session-item ${item.id === session.session_id ? "active" : ""}`} type="button" onClick={() => resetForSession(item.id)}>
              <strong>{item.id}</strong>
              <span>{relativeTime(item.updatedAt)}</span>
            </button>
          ))}
        </div>
      </section>
      <section className="panel">
        <h2 className="panel-title">架构提示</h2>
        <p className="meta-line">Studio 显示同一条 SSE 流中的消息、工具调用、计划推进和审批状态。</p>
      </section>
    </aside>
  );
}

function StateCell({ label, value }: { label: string; value: string }) {
  return <div className="state-cell"><dt>{label}</dt><dd>{value}</dd></div>;
}

function Observer() {
  const session = useAppStore((state) => state.session);
  const events = useAppStore((state) => state.events);
  const toolEvents = events.filter((event) => event.type === "tool_call" || event.type === "tool_result").slice(-12).reverse();
  return (
    <aside className="observer">
      <section className="panel">
        <div className="panel-header"><h2 className="panel-title">当前计划</h2></div>
        <PlanStepper />
      </section>
      <section className="panel">
        <h2 className="panel-title">学习目标</h2>
        <p className="meta-line">{session.learning_target || "当前会话尚未设定学习目标"}</p>
      </section>
      <section className="panel tool-timeline">
        <div className="panel-header"><h2 className="panel-title">Tool 活动</h2></div>
        <div className="tool-timeline">
          {toolEvents.length ? toolEvents.map((event) => <ToolTimelineItem key={event.id} event={event} />) : <div className="empty-card">暂无工具活动</div>}
        </div>
      </section>
    </aside>
  );
}

function ToolTimelineItem({ event }: { event: TraceEvent }) {
  const data = event.data;
  return (
    <article className="tool-timeline-item">
      <strong>{String(data.tool || event.type)}</strong>
      <code>{normalizeAgent(data.agent || event.agent)} · {formatTime(event.timestamp)}</code>
      <p className="meta-line">{event.type === "tool_call" ? pretty(data.args).slice(0, 140) : String(data.content || "").slice(0, 140)}</p>
    </article>
  );
}

function ChatPane({ mode }: { mode: "studio" | "learner" }) {
  const session = useAppStore((state) => state.session);
  const showLearnerPlan = useAppStore((state) => state.showLearnerPlan);
  const setShowLearnerPlan = useAppStore((state) => state.setShowLearnerPlan);
  const refreshLearning = useRefreshLearning();
  return (
    <section className={`chat-pane ${mode === "learner" && showLearnerPlan ? "with-plan" : ""}`}>
      <div className="chat-header">
        <div>
          <p className="eyebrow">{mode === "learner" ? "Learner Chat" : "Studio"}</p>
          <h2 className="section-title">{session.learning_target || session.session_id}</h2>
        </div>
        <div className="toolbar-group">
          <button className="text-button" type="button" onClick={refreshLearning}><RefreshCcw size={16} />刷新</button>
          {mode === "learner" && (
            <button className="text-button" type="button" onClick={() => setShowLearnerPlan(!showLearnerPlan)}>
              <Layers size={16} />{showLearnerPlan ? "隐藏编排" : "显示编排"}
            </button>
          )}
        </div>
      </div>
      {mode === "learner" && showLearnerPlan && <section className="panel"><PlanStepper /></section>}
      {mode === "learner" && normalizeAgent(session.current_agent) === "examination" ? <QuizTakeover /> : <MessageList />}
      <ApprovalDrawer />
      <Composer />
    </section>
  );
}

function MessageList() {
  const messages = useAppStore((state) => state.messages);
  const hasNew = useAppStore((state) => state.hasNewMessageContent);
  const setHasNew = useAppStore((state) => state.setHasNewMessageContent);
  const ref = useRef<HTMLDivElement | null>(null);
  const scrollTop = useRef(0);
  const [atBottom, setAtBottom] = useState(true);

  useLayoutEffect(() => {
    if (!ref.current) return;
    ref.current.scrollTop = scrollTop.current;
    setAtBottom(isAtBottom(ref.current));
  }, [messages]);

  return (
    <>
      <div
        ref={ref}
        className="messages"
        data-messages
        onScroll={(event) => {
          scrollTop.current = event.currentTarget.scrollTop;
          const bottom = isAtBottom(event.currentTarget);
          setAtBottom(bottom);
          if (bottom) setHasNew(false);
        }}
      >
        {messages.length ? messages.map((message, index) => <MessageBubble key={message.id} message={message} previous={messages[index - 1]} />) : <EmptyState />}
      </div>
      {!atBottom && hasNew && (
        <button
          className="new-content-button"
          type="button"
          onClick={() => {
            if (!ref.current) return;
            ref.current.scrollTop = ref.current.scrollHeight;
            scrollTop.current = ref.current.scrollTop;
            setHasNew(false);
            setAtBottom(true);
          }}
        >
          ↓ 有新内容
        </button>
      )}
    </>
  );
}

function EmptyState() {
  const { send } = useChatStream();
  const prompts = [
    "帮我读一下 LangGraph 的 StateGraph 文档并讲讲它",
    "总结 FastAPI 依赖注入的使用方式",
    "出几道题检查我是否理解 RAG 检索流程"
  ];
  return (
    <div className="empty-state">
      <strong>开始一次技术文档研读</strong>
      <p>输入文档主题或学习目标，系统会生成计划并展示 agent 接力过程。</p>
      <div className="starter-grid">
        {prompts.map((prompt) => <button key={prompt} type="button" onClick={() => send(prompt)}>{prompt}</button>)}
      </div>
    </div>
  );
}

function MessageBubble({ message, previous }: { message: ChatMessage; previous?: ChatMessage }) {
  const toolCalls = useAppStore((state) => state.toolCalls);
  const agent = normalizeAgent(message.agent);
  const agentBreak = message.role === "assistant" && previous?.role === "assistant" && normalizeAgent(previous.agent) !== agent;
  return (
    <article className={`message-group ${message.role} ${agentBreak ? "agent-break" : ""}`} style={agentStyle(agent)}>
      <div className="message-meta">
        {message.role === "assistant" ? <><AgentBadge agent={agent} />{message.streaming ? "生成中" : "agent message"}</> : message.role === "user" ? "你" : "system"}
      </div>
      <div className="message-bubble">
        <div className={`message-content ${message.streaming ? "streaming-cursor" : ""}`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content || ""}</ReactMarkdown>
        </div>
        {message.toolCallIds.length > 0 && (
          <div className="tool-stack">
            {message.toolCallIds.map((id) => toolCalls[id] ? <ToolCallCard key={id} tool={toolCalls[id]} /> : null)}
          </div>
        )}
      </div>
    </article>
  );
}

function AgentBadge({ agent }: { agent: AgentKey }) {
  return <span className="agent-badge" style={agentStyle(agent)}><i className="agent-dot" />{agentMeta[agent].label}</span>;
}

function ToolCallCard({ tool }: { tool: ToolCall }) {
  const expanded = useAppStore((state) => state.expandedToolIds.has(tool.id));
  const toggle = useAppStore((state) => state.toggleToolExpanded);
  return (
    <details className="tool-card" style={agentStyle(tool.agent)} open={expanded} onToggle={() => toggle(tool.id)}>
      <summary>
        <span className="tool-title"><ToolCase size={18} /><span>{tool.tool}</span></span>
        <AgentBadge agent={tool.agent} />
        <span className={`tool-status ${tool.status}`}>
          {tool.status === "done" ? <Check size={13} /> : tool.status === "error" ? <X size={13} /> : <RefreshCcw size={13} />}
          {tool.status === "done" ? "完成" : tool.status === "error" ? "错误" : "调用中"}
        </span>
        <span className="tool-chevron">▶</span>
      </summary>
      <div className="tool-body">
        <ToolSection label="args" content={pretty(tool.args)} />
        <ToolSection label="result" content={tool.result ? pretty(tool.result) : "等待工具返回..."} />
      </div>
    </details>
  );
}

function ToolSection({ label, content }: { label: string; content: string }) {
  return (
    <section>
      <div className="tool-section-header">
        <strong>{label}</strong>
        <button className="tool-copy-button" type="button" onClick={() => navigator.clipboard.writeText(content)}><Copy size={13} />复制</button>
      </div>
      <pre className="json-block">{content}</pre>
    </section>
  );
}

function PlanStepper() {
  const plan = useAppStore((state) => state.session.workflow_plan);
  const index = useAppStore((state) => state.session.plan_index);
  if (!plan.length) return <div className="empty-card">暂无计划</div>;
  return (
    <div className="plan-stepper">
      {plan.map((step, stepIndex) => {
        const agent = normalizeAgent(step);
        return (
          <button key={`${step}-${stepIndex}`} className={`plan-step ${stepIndex < index ? "done" : ""} ${stepIndex === index ? "current" : ""}`} style={agentStyle(agent)} type="button">
            <span className="step-node">{stepIndex < index ? "✓" : stepIndex + 1}</span>
            <strong>{agentMeta[agent].label}</strong>
            <small>{stepIndex < index ? "done" : stepIndex === index ? "active" : "queued"}</small>
          </button>
        );
      })}
    </div>
  );
}

function ApprovalDrawer() {
  const pending = useAppStore((state) => state.session.pending_interrupt);
  const running = useAppStore((state) => state.running);
  const toolCalls = useAppStore((state) => state.toolCalls);
  const [feedback, setFeedback] = useState("");
  const { approve } = useChatStream();
  const tool = Object.values(toolCalls).filter((item) => item.status === "pending").sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  return (
    <section className={`approval-drawer ${pending ? "" : "hidden"}`}>
      <div>
        <p className="approval-title">需要确认敏感操作</p>
        <p className="approval-copy">{tool ? `${tool.agent} 请求执行 ${tool.tool}` : "后端正在等待你批准当前工具调用。"}</p>
      </div>
      <textarea value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="拒绝时可填写反馈，例如：换一种检索范围" />
      <div className="approval-actions">
        <button className="danger-button" type="button" disabled={running} onClick={() => approve(false, feedback)}><X size={16} />拒绝</button>
        <button className="primary-button" type="button" disabled={running} onClick={() => approve(true)}><Check size={16} />批准</button>
      </div>
    </section>
  );
}

function Composer() {
  const running = useAppStore((state) => state.running);
  const pending = useAppStore((state) => state.session.pending_interrupt);
  const { send } = useChatStream();
  const [value, setValue] = useState("");
  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        const text = value.trim();
        if (!text) return;
        setValue("");
        send(text);
      }}
    >
      <textarea
        rows={1}
        value={value}
        disabled={running || pending}
        placeholder={pending ? "请先处理审批" : "输入文档链接、技术主题或你的问题..."}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") event.currentTarget.form?.requestSubmit();
        }}
      />
      <button className="send-button" type="submit" disabled={running || pending}><Send size={16} /></button>
    </form>
  );
}

function Learner() {
  return (
    <div className="learner-layout">
      <LearnerHero />
      <div className="learner-grid">
        <KnowledgeRail />
        <ChatPane mode="learner" />
        <ReviewRail />
      </div>
    </div>
  );
}

function LearnerHero() {
  const learning = useAppStore((state) => state.learning);
  return (
    <section className="hero">
      <div>
        <p className="eyebrow">Learning Overview</p>
        <h2 className="hero-title">{learning.total ? `你已经掌握 ${learning.total} 个知识点，${learning.needs_review_count} 个建议复习` : "还没有学习记录，开始你的第一次研读吧"}</h2>
        <p className="hero-copy">学习台直接读取后端学习记录，点击知识卡可以启动复习对话。</p>
      </div>
      <div className="hero-metrics">
        <Metric label="总知识" value={String(learning.total)} />
        <Metric label="平均掌握度" value={`${Math.round((learning.average_score || 0) * 100)}%`} />
        <Metric label="建议复习" value={String(learning.needs_review_count)} />
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric-card"><span>{label}</span><strong>{value}</strong></div>;
}

function KnowledgeRail() {
  const records = useAppStore((state) => state.learning.records);
  return (
    <aside className="knowledge-rail">
      <div className="panel-header"><h2 className="panel-title">我的知识库</h2></div>
      <div className="knowledge-list">
        {records.length ? records.map((record) => <KnowledgeCard key={record.knowledge} record={record} />) : <div className="empty-card">暂无学习记录</div>}
      </div>
    </aside>
  );
}

function KnowledgeCard({ record }: { record: LearningRecord }) {
  const { send } = useChatStream();
  const score = Math.max(0, Math.min(1, Number(record.score || 0)));
  return (
    <button className="knowledge-card" type="button" onClick={() => send(`复习一下 ${record.knowledge}`)}>
      <div className="knowledge-head">
        <strong>{record.knowledge}</strong>
        <span className="score-ring" style={{ "--score": score * 100, "--score-color": scoreTone(score) } as CSSProperties}>{Math.round(score * 100)}%</span>
      </div>
      <div className="knowledge-meta"><span>复习 {record.reviewtimes || 0} 次</span><span>{relativeTime(record.timestamp)}</span></div>
    </button>
  );
}

function reviewScore(record: LearningRecord) {
  const score = Number(record.score || 0);
  const age = Math.min(daysSince(record.timestamp) / 30, 1);
  return (1 - score) * 0.6 + age * 0.4;
}

function ReviewRail() {
  const records = useAppStore((state) => state.learning.records);
  const { send } = useChatStream();
  const review = useMemo(() => [...records].filter((record) => Number(record.score || 0) < 0.8 || daysSince(record.timestamp) > 14).sort((a, b) => reviewScore(b) - reviewScore(a)), [records]);
  return (
    <aside className="review-rail">
      <div className="panel-header">
        <h2 className="panel-title">复习队列</h2>
        <button className="primary-button" type="button" onClick={() => send(review.length ? `请围绕这些知识点出题检查我：${review.slice(0, 3).map((record) => record.knowledge).join("、")}` : "请根据我的学习记录出几道复习题")}>
          <Play size={16} />开始复习
        </button>
      </div>
      <div className="review-list">
        {review.length ? review.map((record) => (
          <article className="review-card" key={record.knowledge}>
            <strong>{record.knowledge}</strong>
            <span className="review-priority">优先级 {Math.round(reviewScore(record) * 100)}</span>
            <p className="meta-line">掌握度 {Math.round(Number(record.score || 0) * 100)}% · {relativeTime(record.timestamp)}</p>
          </article>
        )) : <div className="empty-card">暂无需要复习的知识点</div>}
      </div>
    </aside>
  );
}

function QuizTakeover() {
  const latestExam = useAppStore((state) => [...state.messages].reverse().find((message) => message.role === "assistant" && message.agent === "examination"));
  const [answer, setAnswer] = useState("");
  const { send } = useChatStream();
  return (
    <div className="quiz-shell">
      <section className="quiz-card">
        <p className="eyebrow">Examination Mode</p>
        <h2 className="section-title">测验模式</h2>
        <div className="message-content"><ReactMarkdown remarkPlugins={[remarkGfm]}>{latestExam?.content || "等待 examination agent 给出题目。"}</ReactMarkdown></div>
        <textarea className="quiz-answer" value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="在这里作答，提交后会作为普通用户消息继续发送给 agent。" />
        <button className="primary-button" type="button" onClick={() => { const text = answer.trim(); if (text) { setAnswer(""); send(text); } }}><Send size={16} />提交答案</button>
      </section>
    </div>
  );
}

function Inspector() {
  return (
    <div className="inspector-layout">
      <InspectorToolbar />
      <SwimLane />
      <section className="inspector-bottom">
        <EventList />
        <EventDetail />
      </section>
    </div>
  );
}

function InspectorToolbar() {
  const recording = useAppStore((state) => state.recording);
  const inspectorPaused = useAppStore((state) => state.inspectorPaused);
  const filters = useAppStore((state) => state.filters);
  const setRecording = useAppStore((state) => state.setRecording);
  const setInspectorPaused = useAppStore((state) => state.setInspectorPaused);
  const toggleFilter = useAppStore((state) => state.toggleFilter);
  const events = useAppStore((state) => state.events);
  const session = useAppStore((state) => state.session);
  return (
    <section className="inspector-toolbar">
      <div className="toolbar-group">
        <button className={`chip ${recording ? "active" : ""}`} type="button" onClick={() => setRecording(!recording)}>{recording ? <Play size={16} /> : <Pause size={16} />}{recording ? "录制中" : "已停止"}</button>
        <button className={`chip ${inspectorPaused ? "active" : ""}`} type="button" onClick={() => setInspectorPaused(!inspectorPaused)}>{inspectorPaused ? <Play size={16} /> : <Pause size={16} />}{inspectorPaused ? "继续渲染" : "暂停"}</button>
        <button className="chip" type="button" onClick={() => exportTrace(session.session_id, events)}><Download size={16} />导出 JSON</button>
      </div>
      <div className="toolbar-group">
        {EVENT_TYPES.map((type) => <button key={type} className={`chip ${filters.has(type) ? "active" : ""}`} type="button" onClick={() => toggleFilter(type)}>{type}</button>)}
      </div>
    </section>
  );
}

function filteredEvents(events: TraceEvent[], filters: Set<string>) {
  return events.filter((event) => event.type !== "token" && filters.has(event.type));
}

function SwimLane() {
  const allEvents = useAppStore((state) => state.events);
  const filters = useAppStore((state) => state.filters);
  const running = useAppStore((state) => state.running);
  const currentAgent = useAppStore((state) => normalizeAgent(state.session.current_agent));
  const selected = useAppStore((state) => state.selectedEventId);
  const replaying = useAppStore((state) => state.replayingEventId);
  const setSelected = useAppStore((state) => state.setSelectedEventId);
  const ref = useRef<HTMLElement | null>(null);
  const scroll = useRef({ top: 0, left: 0 });
  const events = filteredEvents(allEvents, filters);
  const bounds = getTimelineBounds(events);

  useLayoutEffect(() => {
    if (!ref.current) return;
    ref.current.scrollTop = scroll.current.top;
    ref.current.scrollLeft = scroll.current.left;
  }, [events.length, selected, replaying]);

  return (
    <section
      ref={ref}
      className="swim-lane"
      onScroll={(event) => { scroll.current = { top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft }; }}
    >
      {(Object.keys(agentMeta) as AgentKey[]).map((agent) => (
        <div className="lane-row" key={agent}>
          <div className="lane-label"><AgentBadge agent={agent} /></div>
          <div className="lane-track">
            {events.filter((event) => event.agent === agent).map((event) => (
              <LaneMarker key={event.id} event={event} bounds={bounds} selected={selected === event.id || replaying === event.id} onSelect={() => setSelected(event.id)} />
            ))}
            {running && currentAgent === agent && <span className="lane-live" style={agentStyle(agent)} />}
          </div>
        </div>
      ))}
    </section>
  );
}

function LaneMarker({ event, bounds, selected, onSelect }: { event: TraceEvent; bounds: { min: number; max: number }; selected: boolean; onSelect: () => void }) {
  const meta = event.data.meta as { stream_started_at?: string; streamed_token_count?: number } | undefined;
  const x = positionForTime(event.timestamp, bounds);
  const segment = event.type === "agent_message" && meta?.stream_started_at
    ? {
      start: positionForTime(meta.stream_started_at, bounds),
      width: Math.max(1.5, x - positionForTime(meta.stream_started_at, bounds))
    }
    : null;
  return (
    <>
      {segment && <span className="lane-segment" style={{ ...agentStyle(event.agent), "--x": segment.start, "--w": segment.width } as CSSProperties} title={`${event.agent} stream · ${meta?.streamed_token_count || 0} tokens`} />}
      <button
        className={`lane-marker ${laneMarkerClass(event)} ${selected ? "selected" : ""}`}
        style={{ ...agentStyle(event.agent), "--x": x } as CSSProperties}
        title={`${event.type}: ${eventSummary(event)}`}
        type="button"
        onClick={onSelect}
      />
    </>
  );
}

function getTimelineBounds(events: TraceEvent[]) {
  const times: number[] = [];
  events.forEach((event) => {
    const time = new Date(event.timestamp).getTime();
    if (Number.isFinite(time)) times.push(time);
    const meta = event.data.meta as { stream_started_at?: string } | undefined;
    const start = new Date(meta?.stream_started_at || "").getTime();
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

function positionForTime(timestamp: string, bounds: { min: number; max: number }) {
  const time = new Date(timestamp).getTime();
  if (!Number.isFinite(time)) return 0;
  return Math.max(0, Math.min(100, ((time - bounds.min) / (bounds.max - bounds.min)) * 100));
}

function laneMarkerClass(event: TraceEvent) {
  if (event.type === "agent_transition") return `agent_transition transition-${String(event.data.phase || "enter")}`;
  if (event.type === "tool_result" && /error|exception|traceback/i.test(String(event.data.content || ""))) return "tool_result tool_result_error";
  return event.type;
}

function EventList() {
  const allEvents = useAppStore((state) => state.events);
  const filters = useAppStore((state) => state.filters);
  const selected = useAppStore((state) => state.selectedEventId);
  const setSelected = useAppStore((state) => state.setSelectedEventId);
  const events = filteredEvents(allEvents, filters);
  const ref = useRef<HTMLDivElement | null>(null);
  const top = useRef(0);
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = top.current;
  }, [events.length, selected]);
  return (
    <div className="inspector-pane">
      <div className="panel-header"><h2 className="panel-title">事件列表</h2></div>
      <div className="event-list" ref={ref} onScroll={(event) => { top.current = event.currentTarget.scrollTop; }}>
        {events.length ? events.map((event) => (
          <button key={event.id} className={`event-row ${event.id === selected ? "selected" : ""}`} style={agentStyle(event.agent)} type="button" onClick={() => setSelected(event.id)}>
            <code>{formatTime(event.timestamp)}</code>
            <span>{event.agent}</span>
            <span className="event-summary"><span className="event-type" style={{ background: "color-mix(in srgb, var(--agent-color) 18%, transparent)", color: "var(--agent-color)" }}>{event.type}</span> {eventSummary(event)}</span>
          </button>
        )) : <div className="empty-card">暂无事件</div>}
      </div>
    </div>
  );
}

function EventDetail() {
  const allEvents = useAppStore((state) => state.events);
  const filters = useAppStore((state) => state.filters);
  const selected = useAppStore((state) => state.selectedEventId);
  const events = filteredEvents(allEvents, filters);
  const event = events.find((item) => item.id === selected) || events[events.length - 1];
  const ref = useRef<HTMLDivElement | null>(null);
  const topByEvent = useRef<Record<string, number>>({});
  useLayoutEffect(() => {
    if (!ref.current) return;
    ref.current.scrollTop = event ? topByEvent.current[event.id] || 0 : 0;
  }, [event?.id]);
  return (
    <div
      className="detail-pane"
      ref={ref}
      onScroll={(scrollEvent) => {
        if (event) topByEvent.current[event.id] = scrollEvent.currentTarget.scrollTop;
      }}
    >
      <div className="panel-header"><h2 className="panel-title">事件详情</h2></div>
      {event ? (
        <>
          <div className="toolbar-group">
            <button className="text-button" type="button" onClick={() => navigator.clipboard.writeText(JSON.stringify(event, null, 2))}><Copy size={16} />复制 JSON</button>
          </div>
          <pre className="json-block">{JSON.stringify(event, null, 2)}</pre>
        </>
      ) : <div className="detail-empty">选择一个事件查看 payload</div>}
    </div>
  );
}

function eventSummary(event: TraceEvent) {
  const data = event.data;
  if (event.type === "tool_call") return `${String(data.tool || "tool")} call`;
  if (event.type === "tool_result") return `${String(data.tool || "tool")} result`;
  if (event.type === "agent_transition") return `${String(data.phase || "")} ${normalizeAgent(data.agent)}`;
  if (event.type === "plan_update") return `plan_index ${String(data.plan_index ?? "-")}`;
  if (event.type === "agent_message") {
    const meta = data.meta as { streamed_token_count?: number; stream_duration_ms?: number } | undefined;
    const suffix = meta ? ` (stream · ${meta.streamed_token_count || 0} tokens · ${((meta.stream_duration_ms || 0) / 1000).toFixed(1)}s)` : "";
    return `${String(data.content || "").slice(0, 70)}${suffix}`;
  }
  if (event.type === "session_snapshot") return "baseline snapshot";
  if (event.type === "interrupt_required") return "approval required";
  if (event.type === "done") return "stream done";
  if (event.type === "error") return String(data.message || "error");
  return event.type;
}

function exportTrace(sessionId: string, events: TraceEvent[]) {
  const payload = {
    session_id: sessionId,
    events,
    exportedAt: new Date().toISOString()
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `trace_${sessionId}_${Date.now()}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function useRefreshLearning() {
  const sessionId = useAppStore((state) => state.session.session_id);
  const setSessionState = useAppStore((state) => state.setSessionState);
  const setLearning = useAppStore((state) => state.setLearning);
  return async () => {
    const [state, learning] = await Promise.all([getSessionState(sessionId), getLearningOverview()]);
    setSessionState(state);
    setLearning(learning);
  };
}

function ToastHost() {
  return <div className="toast-host" aria-live="polite" />;
}
