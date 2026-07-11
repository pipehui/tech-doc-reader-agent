import { fetchEventSource } from "@microsoft/fetch-event-source";
import { normalizeAgent } from "./agentColors";
import { API_BASE, tenantHeaders } from "./shared/api/client";
import {
  getLearningOverview,
  getSessionState
} from "./shared/api/sessionApi";
import { useAppStore } from "./store";
import { refreshSessionContext } from "./features/session/refreshSessionContext";
import {
  createStreamReducerState,
  reduceSseMessage
} from "./streaming/sseReducer";
import { parseSseMessage } from "./streaming/sseEnvelope";
import { dispatchStreamActions } from "./streaming/storeAdapter";
import { sessionTenant } from "./tenant";
import { uid } from "./utils";


class FatalStreamError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FatalStreamError";
  }
}


function formatHttpError(response: Response, payload: unknown) {
  if (payload && typeof payload === "object") {
    const data = payload as Record<string, unknown>;
    if (data.error === "guardrail_blocked") {
      const findings = Array.isArray(data.findings)
        ? data.findings.map(String).join(", ")
        : "";
      return findings
        ? `输入被安全策略拦截：${findings}`
        : "输入被安全策略拦截。";
    }
    if (typeof data.message === "string" && data.message.trim()) {
      return data.message;
    }
    if (typeof data.error === "string" && data.error.trim()) {
      return data.error;
    }
  }
  if (typeof payload === "string" && payload.trim()) {
    return payload;
  }
  return `${response.status} ${response.statusText}`;
}


async function streamErrorFromResponse(response: Response) {
  const contentType = response.headers.get("content-type") || "";
  try {
    if (contentType.includes("application/json")) {
      return new FatalStreamError(
        formatHttpError(response, await response.json())
      );
    }
    return new FatalStreamError(
      formatHttpError(response, await response.text())
    );
  } catch {
    return new FatalStreamError(`${response.status} ${response.statusText}`);
  }
}


async function refreshStateAndLearning(sessionId: string) {
  const store = useAppStore.getState();
  const tenant = sessionTenant(store.session);
  await refreshSessionContext({
    sessionId,
    tenant,
    api: { getSessionState, getLearningOverview },
    store
  });
}


export function useChatStream() {
  async function run(
    path: "/chat" | "/chat/approve",
    body: Record<string, unknown>,
    label: string
  ) {
    const store = useAppStore.getState();
    const sessionId = store.session.session_id;
    const tenant = sessionTenant(store.session);
    const responseId = uid();
    let reducerState = createStreamReducerState(
      responseId,
      normalizeAgent(store.session.current_agent),
      store.toolCalls
    );

    store.setRunning(true, label);
    try {
      await fetchEventSource(`${API_BASE}${path}`, {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          ...tenantHeaders(tenant)
        },
        body: JSON.stringify({
          ...body,
          user_id: tenant.user_id,
          namespace: tenant.namespace
        }),
        openWhenHidden: true,
        async onopen(response) {
          if (!response.ok) {
            throw await streamErrorFromResponse(response);
          }
          const contentType = response.headers.get("content-type") || "";
          if (!contentType.includes("text/event-stream")) {
            throw new FatalStreamError(
              `后端返回了非 SSE 响应：${contentType || "unknown"}`
            );
          }
        },
        onmessage(message) {
          const reduction = reduceSseMessage(
            reducerState,
            parseSseMessage(message.event || "message", message.data),
            { now: new Date().toISOString(), createId: uid }
          );
          reducerState = reduction.state;
          dispatchStreamActions(reduction.actions, useAppStore.getState());
        },
        onerror(error) {
          throw error;
        }
      });
      store.finishResponse(responseId);
      await refreshStateAndLearning(sessionId);
    } catch (error) {
      store.finishResponse(responseId);
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
      store.rememberSession(
        store.session.session_id,
        sessionTenant(store.session)
      );
      return run(
        "/chat",
        { session_id: store.session.session_id, message: text },
        "生成中"
      );
    },
    approve(approved: boolean, feedback = "") {
      const store = useAppStore.getState();
      if (store.running) return;
      return run(
        "/chat/approve",
        { session_id: store.session.session_id, approved, feedback },
        approved ? "继续执行" : "提交反馈"
      );
    }
  };
}
