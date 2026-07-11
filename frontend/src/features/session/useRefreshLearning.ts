import { useMemo } from "react";
import {
  getLearningOverview,
  getSessionState
} from "../../shared/api/sessionApi";
import { useAppStore } from "../../store";
import { sessionTenant } from "../../tenant";
import { refreshSessionContext } from "./refreshSessionContext";


const refreshApi = { getSessionState, getLearningOverview };


export function useRefreshLearning() {
  const session = useAppStore((state) => state.session);
  const setSessionState = useAppStore((state) => state.setSessionState);
  const setLearning = useAppStore((state) => state.setLearning);
  const addSystemMessage = useAppStore((state) => state.addSystemMessage);
  const tenant = useMemo(
    () => sessionTenant(session),
    [session.user_id, session.namespace]
  );

  return () => refreshSessionContext({
    sessionId: session.session_id,
    tenant,
    api: refreshApi,
    store: { setSessionState, setLearning, addSystemMessage }
  });
}
