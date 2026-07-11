import { useMemo, useState } from "react";
import { Check, X } from "lucide-react";
import { useAppStore } from "../../store";
import { useChatStream } from "../../useChatStream";


export function ApprovalDrawer() {
  const pending = useAppStore((state) => state.session.pending_interrupt);
  const running = useAppStore((state) => state.running);
  const toolCalls = useAppStore((state) => state.toolCalls);
  const [feedback, setFeedback] = useState("");
  const { approve } = useChatStream();
  const tool = useMemo(
    () => Object.values(toolCalls)
      .filter((item) => item.status === "pending")
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0],
    [toolCalls]
  );

  return (
    <section className={`approval-drawer ${pending ? "" : "hidden"}`}>
      <div>
        <p className="approval-title">需要确认敏感操作</p>
        <p className="approval-copy">
          {tool
            ? `${tool.agent} 请求执行 ${tool.tool}`
            : "后端正在等待你批准当前工具调用。"}
        </p>
      </div>
      <textarea
        value={feedback}
        onChange={(event) => setFeedback(event.target.value)}
        placeholder="拒绝时可填写反馈，例如：换一种检索范围"
      />
      <div className="approval-actions">
        <button
          className="danger-button"
          type="button"
          disabled={running}
          onClick={() => approve(false, feedback)}
        >
          <X size={16} />拒绝
        </button>
        <button
          className="primary-button"
          type="button"
          disabled={running}
          onClick={() => approve(true)}
        >
          <Check size={16} />批准
        </button>
      </div>
    </section>
  );
}
