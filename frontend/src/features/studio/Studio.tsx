import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Trash2 } from "lucide-react";
import { normalizeAgent } from "../../agentColors";
import { experiencePath } from "../../app/routing";
import { useAppStore } from "../../store";
import { sameTenant, sessionTenant } from "../../tenant";
import type { TraceEvent } from "../../types";
import { formatTime, makeSessionId, pretty, relativeTime } from "../../utils";
import { ChatPane, MessageList, PlanStepper } from "../chat/ChatPane";


export function Studio() {
  return (
    <div className="studio-grid">
      <StudioRail />
      <ChatPane mode="studio"><MessageList /></ChatPane>
      <Observer />
    </div>
  );
}


function StudioRail() {
  const navigate = useNavigate();
  const session = useAppStore((state) => state.session);
  const sessions = useAppStore((state) => state.sessions);
  const messages = useAppStore((state) => state.messages);
  const resetForSession = useAppStore((state) => state.resetForSession);
  const deleteSession = useAppStore((state) => state.deleteSession);
  const tenant = useMemo(
    () => sessionTenant(session),
    [session.user_id, session.namespace]
  );
  const scopedSessions = sessions.filter((item) => sameTenant(item, tenant));
  const entries = scopedSessions.length
    ? scopedSessions
    : [{
      id: session.session_id,
      user_id: tenant.user_id,
      namespace: tenant.namespace,
      updatedAt: new Date().toISOString()
    }];

  function openSession(sessionId: string) {
    resetForSession(sessionId);
    navigate(experiencePath("studio", tenant, sessionId), { replace: true });
  }

  function removeSession(sessionId: string) {
    if (!window.confirm(`删除会话 ${sessionId}？`)) return;
    const fallback = scopedSessions.find((item) => item.id !== sessionId)?.id
      || makeSessionId();
    deleteSession(sessionId, tenant);
    if (sessionId === session.session_id) openSession(fallback);
  }

  return (
    <aside className="rail">
      <section className="panel">
        <div className="panel-header"><h2 className="panel-title">当前会话</h2></div>
        <dl className="state-grid">
          <StateCell label="User" value={tenant.user_id} />
          <StateCell label="Namespace" value={tenant.namespace} />
          <StateCell label="Agent" value={normalizeAgent(session.current_agent)} />
          <StateCell
            label="消息"
            value={String(session.message_count || messages.length)}
          />
          <StateCell label="目标" value={session.learning_target || "-"} />
          <StateCell
            label="审批"
            value={session.pending_interrupt ? "待确认" : "无"}
          />
        </dl>
      </section>
      <section className="panel session-list">
        <div className="panel-header"><h2 className="panel-title">会话列表</h2></div>
        <div className="session-list">
          {entries.map((item) => (
            <div
              key={`${item.user_id}:${item.namespace}:${item.id}`}
              className={`session-item ${item.id === session.session_id ? "active" : ""}`}
            >
              <button
                className="session-select"
                type="button"
                onClick={() => openSession(item.id)}
              >
                <strong>{item.id}</strong>
                <span>{relativeTime(item.updatedAt)}</span>
              </button>
              <button
                className="session-delete-button"
                type="button"
                title="删除会话"
                onClick={() => removeSession(item.id)}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
      </section>
      <section className="panel">
        <h2 className="panel-title">架构提示</h2>
        <p className="meta-line">
          Studio 显示同一条 SSE 流中的消息、工具调用、计划推进和审批状态。
        </p>
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
  const toolEvents = events
    .filter((event) => event.type === "tool_call" || event.type === "tool_result")
    .slice(-12)
    .reverse();

  return (
    <aside className="observer">
      <section className="panel">
        <div className="panel-header"><h2 className="panel-title">当前计划</h2></div>
        <PlanStepper />
      </section>
      <section className="panel">
        <h2 className="panel-title">学习目标</h2>
        <p className="meta-line">
          {session.learning_target || "当前会话尚未设定学习目标"}
        </p>
      </section>
      <section className="panel tool-timeline">
        <div className="panel-header"><h2 className="panel-title">Tool 活动</h2></div>
        <div className="tool-timeline">
          {toolEvents.length
            ? toolEvents.map((event) => (
              <ToolTimelineItem key={event.id} event={event} />
            ))
            : <div className="empty-card">暂无工具活动</div>}
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
      <code>
        {normalizeAgent(data.agent || event.agent)} · {formatTime(event.timestamp)}
      </code>
      <p className="meta-line">
        {event.type === "tool_call"
          ? pretty(data.args).slice(0, 140)
          : String(data.content || "").slice(0, 140)}
      </p>
    </article>
  );
}
