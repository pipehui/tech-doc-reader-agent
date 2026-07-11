import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Check,
  Copy,
  Layers,
  RefreshCcw,
  Send,
  ToolCase,
  X
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { agentMeta, agentStyle, normalizeAgent } from "../../agentColors";
import { AgentBadge } from "../../shared/components/AgentBadge";
import { useAppStore } from "../../store";
import type { ChatMessage, ToolCall } from "../../types";
import { useChatStream } from "../../useChatStream";
import { pretty } from "../../utils";
import { ApprovalDrawer } from "../approval/ApprovalDrawer";
import { useRefreshLearning } from "../session/useRefreshLearning";


const STARTER_PROMPTS = [
  "帮我读一下 LangGraph 的 StateGraph 文档并讲讲它",
  "总结 FastAPI 依赖注入的使用方式",
  "出几道题检查我是否理解 RAG 检索流程"
] as const;


function isAtBottom(element: HTMLElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight < 80;
}


export function ChatPane({
  mode,
  showPlan = false,
  onTogglePlan,
  children
}: {
  mode: "studio" | "learner";
  showPlan?: boolean;
  onTogglePlan?: () => void;
  children: ReactNode;
}) {
  const session = useAppStore((state) => state.session);
  const refreshLearning = useRefreshLearning();

  return (
    <section className={`chat-pane ${mode === "learner" && showPlan ? "with-plan" : ""}`}>
      <div className="chat-header">
        <div>
          <p className="eyebrow">{mode === "learner" ? "Learner Chat" : "Studio"}</p>
          <h2 className="section-title">
            {session.learning_target || session.session_id}
          </h2>
        </div>
        <div className="toolbar-group">
          <button className="text-button" type="button" onClick={refreshLearning}>
            <RefreshCcw size={16} />刷新
          </button>
          {mode === "learner" && onTogglePlan && (
            <button className="text-button" type="button" onClick={onTogglePlan}>
              <Layers size={16} />{showPlan ? "隐藏编排" : "显示编排"}
            </button>
          )}
        </div>
      </div>
      {mode === "learner" && showPlan && (
        <section className="panel"><PlanStepper /></section>
      )}
      {children}
      <ApprovalDrawer />
      <Composer />
    </section>
  );
}


export function MessageList() {
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
        {messages.length
          ? messages.map((message, index) => (
            <MessageBubble
              key={message.id}
              message={message}
              previous={messages[index - 1]}
            />
          ))
          : <EmptyState />}
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
  return (
    <div className="empty-state">
      <strong>开始一次技术文档研读</strong>
      <p>输入文档主题或学习目标，系统会生成计划并展示 agent 接力过程。</p>
      <div className="starter-grid">
        {STARTER_PROMPTS.map((prompt) => (
          <button key={prompt} type="button" onClick={() => send(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}


export function MessageBubble({
  message,
  previous
}: {
  message: ChatMessage;
  previous?: ChatMessage;
}) {
  const toolCalls = useAppStore((state) => state.toolCalls);
  const agent = normalizeAgent(message.agent);
  const agentBreak = message.role === "assistant"
    && previous?.role === "assistant"
    && normalizeAgent(previous.agent) !== agent;

  return (
    <article
      className={`message-group ${message.role} ${agentBreak ? "agent-break" : ""}`}
      style={agentStyle(agent)}
    >
      <div className="message-meta">
        {message.role === "assistant"
          ? <><AgentBadge agent={agent} />{message.streaming ? "生成中" : "agent message"}</>
          : message.role === "user" ? "你" : "system"}
      </div>
      <div className="message-bubble">
        <div className={`message-content ${message.streaming ? "streaming-cursor" : ""}`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.content || ""}
          </ReactMarkdown>
        </div>
        {message.toolCallIds.length > 0 && (
          <div className="tool-stack">
            {message.toolCallIds.map((id) => toolCalls[id]
              ? <ToolCallCard key={id} tool={toolCalls[id]} />
              : null)}
          </div>
        )}
      </div>
    </article>
  );
}


export function ToolCallCard({ tool }: { tool: ToolCall }) {
  const expanded = useAppStore((state) => state.expandedToolIds.has(tool.id));
  const toggle = useAppStore((state) => state.toggleToolExpanded);

  return (
    <details
      className="tool-card"
      style={agentStyle(tool.agent)}
      open={expanded}
      onToggle={() => toggle(tool.id)}
    >
      <summary>
        <span className="tool-title"><ToolCase size={18} /><span>{tool.tool}</span></span>
        <AgentBadge agent={tool.agent} />
        <span className={`tool-status ${tool.status}`}>
          {tool.status === "done"
            ? <Check size={13} />
            : tool.status === "error" ? <X size={13} /> : <RefreshCcw size={13} />}
          {tool.status === "done"
            ? "完成"
            : tool.status === "error" ? "错误" : "调用中"}
        </span>
        <span className="tool-chevron">▶</span>
      </summary>
      <div className="tool-body">
        <ToolSection label="args" content={pretty(tool.args)} />
        <ToolSection
          label="result"
          content={tool.result ? pretty(tool.result) : "等待工具返回..."}
        />
      </div>
    </details>
  );
}


function ToolSection({ label, content }: { label: string; content: string }) {
  return (
    <section>
      <div className="tool-section-header">
        <strong>{label}</strong>
        <button
          className="tool-copy-button"
          type="button"
          onClick={() => navigator.clipboard.writeText(content)}
        >
          <Copy size={13} />复制
        </button>
      </div>
      <pre className="json-block">{content}</pre>
    </section>
  );
}


export function PlanStepper() {
  const plan = useAppStore((state) => state.session.workflow_plan);
  const index = useAppStore((state) => state.session.plan_index);
  if (!plan.length) return <div className="empty-card">暂无计划</div>;

  return (
    <div className="plan-stepper">
      {plan.map((step, stepIndex) => {
        const agent = normalizeAgent(step);
        return (
          <button
            key={`${step}-${stepIndex}`}
            className={`plan-step ${stepIndex < index ? "done" : ""} ${stepIndex === index ? "current" : ""}`}
            style={agentStyle(agent)}
            type="button"
          >
            <span className="step-node">
              {stepIndex < index ? "✓" : stepIndex + 1}
            </span>
            <strong>{agentMeta[agent].label}</strong>
            <small>
              {stepIndex < index ? "done" : stepIndex === index ? "active" : "queued"}
            </small>
          </button>
        );
      })}
    </div>
  );
}


function Composer() {
  const location = useLocation();
  const navigate = useNavigate();
  const running = useAppStore((state) => state.running);
  const pending = useAppStore((state) => state.session.pending_interrupt);
  const { send } = useChatStream();
  const [value, setValue] = useState("");

  useEffect(() => {
    if (location.pathname !== "/studio") return;
    const params = new URLSearchParams(location.search);
    const prompt = params.get("prompt");
    if (!prompt) return;
    setValue(prompt);
    params.delete("prompt");
    const nextSearch = params.toString();
    navigate(
      `${location.pathname}${nextSearch ? `?${nextSearch}` : ""}`,
      { replace: true }
    );
  }, [location.pathname, location.search, navigate]);

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
        placeholder={pending
          ? "请先处理审批"
          : "输入文档链接、技术主题或你的问题..."}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.currentTarget.form?.requestSubmit();
          }
        }}
      />
      <button
        className="send-button"
        type="submit"
        disabled={running || pending}
      >
        <Send size={16} />
      </button>
    </form>
  );
}
