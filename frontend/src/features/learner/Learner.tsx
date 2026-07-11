import { useMemo, useState, type CSSProperties } from "react";
import { Play, Send } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { normalizeAgent } from "../../agentColors";
import { useAppStore } from "../../store";
import type { LearningRecord } from "../../types";
import { useChatStream } from "../../useChatStream";
import { daysSince, relativeTime, scoreTone } from "../../utils";
import { ChatPane, MessageList } from "../chat/ChatPane";


export function Learner() {
  const showPlan = useAppStore((state) => state.showLearnerPlan);
  const setShowPlan = useAppStore((state) => state.setShowLearnerPlan);
  const examinationActive = useAppStore(
    (state) => normalizeAgent(state.session.current_agent) === "examination"
  );

  return (
    <div className="learner-layout">
      <LearnerHero />
      <div className="learner-grid">
        <KnowledgeRail />
        <ChatPane
          mode="learner"
          showPlan={showPlan}
          onTogglePlan={() => setShowPlan(!showPlan)}
        >
          {examinationActive ? <QuizTakeover /> : <MessageList />}
        </ChatPane>
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
        <h2 className="hero-title">
          {learning.total
            ? `你已经掌握 ${learning.total} 个知识点，${learning.needs_review_count} 个建议复习`
            : "还没有学习记录，开始你的第一次研读吧"}
        </h2>
        <p className="hero-copy">
          学习台直接读取后端学习记录，点击知识卡可以启动复习对话。
        </p>
      </div>
      <div className="hero-metrics">
        <Metric label="总知识" value={String(learning.total)} />
        <Metric
          label="平均掌握度"
          value={`${Math.round((learning.average_score || 0) * 100)}%`}
        />
        <Metric label="建议复习" value={String(learning.needs_review_count)} />
      </div>
    </section>
  );
}


function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-card"><span>{label}</span><strong>{value}</strong></div>
  );
}


function KnowledgeRail() {
  const records = useAppStore((state) => state.learning.records);
  return (
    <aside className="knowledge-rail">
      <div className="panel-header"><h2 className="panel-title">我的知识库</h2></div>
      <div className="knowledge-list">
        {records.length
          ? records.map((record) => (
            <KnowledgeCard key={record.knowledge} record={record} />
          ))
          : <div className="empty-card">暂无学习记录</div>}
      </div>
    </aside>
  );
}


function KnowledgeCard({ record }: { record: LearningRecord }) {
  const { send } = useChatStream();
  const score = Math.max(0, Math.min(1, Number(record.score || 0)));

  return (
    <button
      className="knowledge-card"
      type="button"
      onClick={() => send(`复习一下 ${record.knowledge}`)}
    >
      <div className="knowledge-head">
        <strong>{record.knowledge}</strong>
        <span
          className="score-ring"
          style={{
            "--score": score * 100,
            "--score-color": scoreTone(score)
          } as CSSProperties}
        >
          {Math.round(score * 100)}%
        </span>
      </div>
      <div className="knowledge-meta">
        <span>复习 {record.reviewtimes || 0} 次</span>
        <span>{relativeTime(record.timestamp)}</span>
      </div>
    </button>
  );
}


export function reviewScore(record: LearningRecord) {
  const score = Number(record.score || 0);
  const age = Math.min(daysSince(record.timestamp) / 30, 1);
  return (1 - score) * 0.6 + age * 0.4;
}


function ReviewRail() {
  const records = useAppStore((state) => state.learning.records);
  const { send } = useChatStream();
  const review = useMemo(
    () => [...records]
      .filter((record) => (
        Number(record.score || 0) < 0.8 || daysSince(record.timestamp) > 14
      ))
      .sort((left, right) => reviewScore(right) - reviewScore(left)),
    [records]
  );

  return (
    <aside className="review-rail">
      <div className="panel-header">
        <h2 className="panel-title">复习队列</h2>
        <button
          className="primary-button"
          type="button"
          onClick={() => send(review.length
            ? `请围绕这些知识点出题检查我：${review.slice(0, 3).map((record) => record.knowledge).join("、")}`
            : "请根据我的学习记录出几道复习题")}
        >
          <Play size={16} />开始复习
        </button>
      </div>
      <div className="review-list">
        {review.length
          ? review.map((record) => (
            <article className="review-card" key={record.knowledge}>
              <strong>{record.knowledge}</strong>
              <span className="review-priority">
                优先级 {Math.round(reviewScore(record) * 100)}
              </span>
              <p className="meta-line">
                掌握度 {Math.round(Number(record.score || 0) * 100)}%
                {" "}· {relativeTime(record.timestamp)}
              </p>
            </article>
          ))
          : <div className="empty-card">暂无需要复习的知识点</div>}
      </div>
    </aside>
  );
}


function QuizTakeover() {
  const latestExam = useAppStore((state) => [...state.messages]
    .reverse()
    .find((message) => (
      message.role === "assistant" && message.agent === "examination"
    )));
  const [answer, setAnswer] = useState("");
  const { send } = useChatStream();

  return (
    <div className="quiz-shell">
      <section className="quiz-card">
        <p className="eyebrow">Examination Mode</p>
        <h2 className="section-title">测验模式</h2>
        <div className="message-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {latestExam?.content || "等待 examination agent 给出题目。"}
          </ReactMarkdown>
        </div>
        <textarea
          className="quiz-answer"
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          placeholder="在这里作答，提交后会作为普通用户消息继续发送给 agent。"
        />
        <button
          className="primary-button"
          type="button"
          onClick={() => {
            const text = answer.trim();
            if (!text) return;
            setAnswer("");
            send(text);
          }}
        >
          <Send size={16} />提交答案
        </button>
      </section>
    </div>
  );
}
