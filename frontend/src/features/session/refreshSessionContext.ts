import type { AppStore } from "../../store/contracts";
import type { LearningOverview, SessionState, TenantScope } from "../../types";


export interface SessionRefreshApi {
  getSessionState: (
    sessionId: string,
    tenant: TenantScope
  ) => Promise<SessionState>;
  getLearningOverview: (
    tenant: TenantScope
  ) => Promise<LearningOverview>;
}

export type SessionRefreshStore = Pick<
  AppStore,
  "setSessionState" | "setLearning" | "addSystemMessage"
>;


export async function refreshSessionContext({
  sessionId,
  tenant,
  api,
  store
}: {
  sessionId: string;
  tenant: TenantScope;
  api: SessionRefreshApi;
  store: SessionRefreshStore;
}) {
  try {
    const [state, learning] = await Promise.all([
      api.getSessionState(sessionId, tenant),
      api.getLearningOverview(tenant)
    ]);
    store.setSessionState(state);
    store.setLearning(learning);
    return "loaded" as const;
  } catch (error) {
    store.addSystemMessage(
      `状态刷新失败：${error instanceof Error ? error.message : String(error)}`
    );
    return "failed" as const;
  }
}
