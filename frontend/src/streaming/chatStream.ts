import type { FetchEventSourceInit } from "@microsoft/fetch-event-source";
import { normalizeAgent } from "../agentColors";
import type { AppStore } from "../store/contracts";
import { sessionTenant } from "../tenant";
import type { TenantScope } from "../types";
import { parseSseMessage } from "./sseEnvelope";
import {
  createStreamReducerState,
  reduceSseMessage
} from "./sseReducer";
import { dispatchStreamActions } from "./storeAdapter";


export type StreamTransport = (
  input: RequestInfo,
  init: FetchEventSourceInit
) => Promise<void>;

export interface ChatStreamDependencies {
  stream: StreamTransport;
  apiBase: string;
  headersForTenant: (tenant: TenantScope) => Record<string, string>;
  getStore: () => AppStore;
  refreshContext: (
    sessionId: string,
    tenant: TenantScope,
    store: AppStore
  ) => Promise<unknown>;
  createId: () => string;
  now: () => string;
}


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
  if (typeof payload === "string" && payload.trim()) return payload;
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


export function createChatStream(dependencies: ChatStreamDependencies) {
  const {
    stream,
    apiBase,
    headersForTenant,
    getStore,
    refreshContext,
    createId,
    now
  } = dependencies;

  async function run(
    path: "/chat" | "/chat/approve",
    body: Record<string, unknown>,
    label: string
  ) {
    const store = getStore();
    const sessionId = store.session.session_id;
    const tenant = sessionTenant(store.session);
    const responseId = createId();
    let reducerState = createStreamReducerState(
      responseId,
      normalizeAgent(store.session.current_agent),
      store.toolCalls
    );

    store.setRunning(true, label);
    try {
      await stream(`${apiBase}${path}`, {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          ...headersForTenant(tenant)
        },
        body: JSON.stringify({
          ...body,
          user_id: tenant.user_id,
          namespace: tenant.namespace
        }),
        openWhenHidden: true,
        async onopen(response) {
          if (!response.ok) throw await streamErrorFromResponse(response);
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
            { now: now(), createId }
          );
          reducerState = reduction.state;
          dispatchStreamActions(reduction.actions, getStore());
        },
        onerror(error) {
          throw error;
        }
      });
      store.finishResponse(responseId);
      await refreshContext(sessionId, tenant, store);
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
      const store = getStore();
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
      const store = getStore();
      if (store.running) return;
      return run(
        "/chat/approve",
        { session_id: store.session.session_id, approved, feedback },
        approved ? "继续执行" : "提交反馈"
      );
    }
  };
}
