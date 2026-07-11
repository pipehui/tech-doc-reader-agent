import { useMemo, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  BookOpen,
  ExternalLink,
  Layers,
  MessageSquare,
  RotateCcw,
  ShieldCheck,
  Zap
} from "lucide-react";
import { experiencePath, GITHUB_URL } from "../../app/routing";
import { useAppStore } from "../../store";
import { sessionTenant } from "../../tenant";
import { makeSessionId } from "../../utils";


const MODE_CARDS = [
  {
    key: "studio",
    title: "Studio",
    subtitle: "工作台",
    description: "日常对话工作台。看到 plan 推进、agent 切换、tool 调用全过程，支持 HITL 审批。",
    chip: "推荐用户从这开始",
    color: "#4F46E5",
    cta: "进入 Studio",
    Icon: MessageSquare
  },
  {
    key: "inspector",
    title: "Inspector",
    subtitle: "追踪台",
    description: "把一次对话当事件流来看：6 通道 swim lane、原始事件列表、JSON 导出。可观测性优先。",
    chip: "推荐工程视角",
    color: "#059669",
    cta: "进入 Inspector",
    Icon: Activity
  },
  {
    key: "learner",
    title: "Learner",
    subtitle: "学习台",
    description: "知识仪表盘 + 测验模式。看自己学过什么、该复习什么，进入测验状态时界面专门变形。",
    chip: "推荐产品视角",
    color: "#E11D48",
    cta: "进入 Learner",
    Icon: BookOpen
  }
] as const;

const HIGHLIGHTS = [
  {
    title: "流式 + 多模型回退",
    description: "基于 SSE 的 token 级流式输出，主模型超时时自动切换到备用模型，全程对用户透明。",
    Icon: Zap
  },
  {
    title: "HITL 审批阻塞",
    description: "敏感工具调用前主动暂停，等待用户审批后继续。审批面板强制阻塞输入框，不可错过。",
    Icon: ShieldCheck
  },
  {
    title: "会话恢复",
    description: "任何时刻刷新页面或断网重连，前端基于 Redis Checkpointer + 状态快照接口完整恢复对话。",
    Icon: RotateCcw
  },
  {
    title: "三视角统一",
    description: "同一份会话状态被 Studio / Inspector / Learner 用三种不同视角呈现，状态共享、心智一致。",
    Icon: Layers
  }
] as const;

const PROMPT_CARDS = [
  {
    title: "解析新概念",
    context: "用户对一个技术点完全陌生",
    prompt: "讲讲 LangGraph 的 StateGraph 是什么，给我一些例子"
  },
  {
    title: "类比已知",
    context: "用户用熟悉概念帮助理解新概念",
    prompt: "我熟悉 React 的 useState，帮我用类似的心智模型理解 Vue 3 的 setup"
  },
  {
    title: "复习掌握度",
    context: "用户启动测验流程",
    prompt: "复习一下我之前学过的内容，给我出几道题"
  }
] as const;


export function Landing() {
  const navigate = useNavigate();
  const session = useAppStore((state) => state.session);
  const tenant = useMemo(
    () => sessionTenant(session),
    [session.user_id, session.namespace]
  );

  return (
    <div className="landing-page">
      <section className="landing-hero">
        <div className="landing-hero-content">
          <p className="landing-kicker">LANGGRAPH · MULTI-AGENT · SSE · FASTAPI</p>
          <h2>把一份陌生的技术文档<br />读透、记住、能讲清</h2>
          <p>
            一个基于 LangGraph 的多智能体系统，把"读完一份技术文档"
            变成解析 → 类比 → 讲解 → 测验 → 沉淀的协作流程。
          </p>
          <div className="landing-cta-row">
            <button
              className="landing-primary-cta"
              type="button"
              onClick={() => navigate(experiencePath("studio", tenant))}
            >
              开始体验
              <ArrowRight size={17} />
            </button>
            <a
              className="landing-secondary-cta"
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
            >
              查看源码
              <ExternalLink size={17} />
            </a>
          </div>
        </div>
      </section>

      <section className="landing-modes" aria-label="三种体验形态">
        {MODE_CARDS.map(({
          key,
          title,
          subtitle,
          description,
          chip,
          color,
          cta,
          Icon
        }) => (
          <button
            key={key}
            className="mode-card"
            style={{ "--landing-accent": color } as CSSProperties}
            type="button"
            onClick={() => navigate(experiencePath(key, tenant))}
          >
            <span className="mode-accent" />
            <span className="mode-head">
              <span className="mode-icon"><Icon size={22} strokeWidth={2} /></span>
              <span>
                <strong>{title}</strong>
                <small>{subtitle}</small>
              </span>
            </span>
            <span className="mode-copy">{description}</span>
            <span className="mode-chip">{chip}</span>
            <span className="mode-link">{cta}<ArrowRight size={16} /></span>
          </button>
        ))}
      </section>

      <section className="landing-section architecture-section">
        <div className="landing-section-head">
          <p className="eyebrow">系统架构</p>
          <h2>Adaptive Routing × Multi-Agent Orchestration</h2>
          <p>
            Primary Assistant 根据用户意图自适应选择三条路径之一：
            Direct（直接回复）、Single Agent（单步）、或 Agent Chain（链式）。
          </p>
        </div>
        <div className="architecture-figure">
          <img
            src="/graphs/tech_doc_reader_agent_architecture.svg"
            alt="技术文档研读助手系统架构图"
          />
        </div>
        <button
          className="landing-text-link"
          type="button"
          onClick={() => navigate(experiencePath("inspector", tenant))}
        >
          在 Inspector 看真实事件流
          <ArrowRight size={16} />
        </button>
      </section>

      <section className="landing-section highlights-section">
        <div className="landing-section-head compact">
          <p className="eyebrow">工程亮点</p>
          <h2>四张工程王牌</h2>
        </div>
        <div className="highlight-grid">
          {HIGHLIGHTS.map(({ title, description, Icon }) => (
            <article className="highlight-item" key={title}>
              <Icon size={24} strokeWidth={2} />
              <h3>{title}</h3>
              <p>{description}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section quickstart-section">
        <div className="landing-section-head compact">
          <p className="eyebrow">快速开始</p>
          <h2>一键开始</h2>
        </div>
        <div className="prompt-grid">
          {PROMPT_CARDS.map(({ title, context, prompt }) => (
            <button
              key={title}
              className="prompt-card"
              type="button"
              onClick={() => navigate(
                experiencePath("studio", tenant, makeSessionId(), prompt)
              )}
            >
              <span>{title}</span>
              <small>{context}</small>
              <code>"{prompt}"</code>
            </button>
          ))}
        </div>
      </section>

      <footer className="landing-footer">
        <p>
          pipehui · 2026 · <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub</a>
          {" "}· Built with LangGraph + FastAPI + React
        </p>
      </footer>
    </div>
  );
}
