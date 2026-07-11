import { fetchEventSource } from "@microsoft/fetch-event-source";
import { refreshSessionContext } from "./features/session/refreshSessionContext";
import { API_BASE, tenantHeaders } from "./shared/api/client";
import {
  getLearningOverview,
  getSessionState
} from "./shared/api/sessionApi";
import { useAppStore } from "./store";
import { createChatStream } from "./streaming/chatStream";
import { uid } from "./utils";


const chatStream = createChatStream({
  stream: fetchEventSource,
  apiBase: API_BASE,
  headersForTenant: tenantHeaders,
  getStore: useAppStore.getState,
  refreshContext(sessionId, tenant, store) {
    return refreshSessionContext({
      sessionId,
      tenant,
      api: { getSessionState, getLearningOverview },
      store
    });
  },
  createId: uid,
  now: () => new Date().toISOString()
});


export function useChatStream() {
  return chatStream;
}
