import { normalizeAgent } from "../../agentColors";
import type { AppStore } from "../../store/contracts";
import type {
  ChatMessage,
  HistoryResponse,
  LearningOverview,
  SessionState,
  TenantScope
} from "../../types";
import { uid } from "../../utils";


export interface SessionLoadOptions {
  signal: AbortSignal;
}

export interface SessionBootstrapApi {
  getSessionState: (
    sessionId: string,
    tenant: TenantScope,
    options: SessionLoadOptions
  ) => Promise<SessionState>;
  getSessionHistory: (
    sessionId: string,
    tenant: TenantScope,
    options: SessionLoadOptions
  ) => Promise<HistoryResponse>;
  getLearningOverview: (
    tenant: TenantScope,
    options: SessionLoadOptions
  ) => Promise<LearningOverview>;
}

export type SessionBootstrapStore = Pick<
  AppStore,
  | "hydrateTranscript"
  | "setSessionState"
  | "setMessages"
  | "setLearning"
  | "addSystemMessage"
>;

export interface MessageFactory {
  createId?: () => string;
  now?: () => string;
}

export type SessionLoadResult = "loaded" | "aborted" | "failed";


export function historyToMessages(
  history: HistoryResponse,
  state: SessionState,
  factory: MessageFactory = {}
): ChatMessage[] {
  const createId = factory.createId ?? uid;
  const now = factory.now ?? (() => new Date().toISOString());
  return (history.messages || []).map((item) => ({
    id: item.id || createId(),
    role: item.role,
    agent: normalizeAgent(item.name || state.current_agent || "primary"),
    content: item.content || "",
    streaming: false,
    toolCallIds: [],
    createdAt: now()
  }));
}


export function isAbortError(error: unknown) {
  return error instanceof DOMException
    ? error.name === "AbortError"
    : error instanceof Error && error.name === "AbortError";
}


export async function loadSessionContext({
  sessionId,
  tenant,
  signal,
  api,
  store,
  messageFactory
}: {
  sessionId: string;
  tenant: TenantScope;
  signal: AbortSignal;
  api: SessionBootstrapApi;
  store: SessionBootstrapStore;
  messageFactory?: MessageFactory;
}): Promise<SessionLoadResult> {
  const hasCachedTranscript = store.hydrateTranscript(sessionId, tenant);

  try {
    const options = { signal };
    const [state, history, learning] = await Promise.all([
      api.getSessionState(sessionId, tenant, options),
      api.getSessionHistory(sessionId, tenant, options),
      api.getLearningOverview(tenant, options)
    ]);

    // Some HTTP adapters do not honor AbortSignal. Keep the write boundary safe
    // even when an obsolete request resolves successfully after a context switch.
    if (signal.aborted) return "aborted";

    store.setSessionState(state);
    if (!hasCachedTranscript) {
      store.setMessages(historyToMessages(history, state, messageFactory));
    }
    store.setLearning(learning);
    return "loaded";
  } catch (error) {
    if (signal.aborted || isAbortError(error)) return "aborted";
    store.addSystemMessage(
      `会话恢复失败：${error instanceof Error ? error.message : String(error)}`
    );
    return "failed";
  }
}
