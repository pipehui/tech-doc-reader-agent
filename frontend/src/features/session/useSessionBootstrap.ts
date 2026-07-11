import { useEffect, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  getLearningOverview,
  getSessionHistory,
  getSessionState
} from "../../shared/api/sessionApi";
import { useAppStore } from "../../store";
import {
  applyTenantSearchParams,
  sameTenant,
  sessionTenant,
  tenantFromSearchParams
} from "../../tenant";
import type { TenantScope } from "../../types";
import { loadSessionContext } from "./sessionBootstrap";


const sessionApi = {
  getSessionState,
  getSessionHistory,
  getLearningOverview
};


export function isUrlContextReady(
  params: URLSearchParams,
  sessionId: string,
  tenant: TenantScope
) {
  return params.get("session") === sessionId
    && params.get("user_id") === tenant.user_id
    && params.get("namespace") === tenant.namespace;
}


export function useSessionBootstrap(enabled: boolean) {
  const location = useLocation();
  const navigate = useNavigate();
  const session = useAppStore((state) => state.session);
  const hydrateTranscript = useAppStore((state) => state.hydrateTranscript);
  const setSessionState = useAppStore((state) => state.setSessionState);
  const setMessages = useAppStore((state) => state.setMessages);
  const setLearning = useAppStore((state) => state.setLearning);
  const addSystemMessage = useAppStore((state) => state.addSystemMessage);
  const rememberSession = useAppStore((state) => state.rememberSession);
  const resetForContext = useAppStore((state) => state.resetForContext);
  const tenant = useMemo(
    () => sessionTenant(session),
    [session.user_id, session.namespace]
  );

  useEffect(() => {
    if (!enabled) return;
    const params = new URLSearchParams(location.search);
    const urlSession = params.get("session");
    if (!urlSession) return;
    const urlTenant = tenantFromSearchParams(params);
    if (urlSession !== session.session_id || !sameTenant(urlTenant, tenant)) {
      resetForContext(urlSession, urlTenant);
    }
  }, [
    enabled,
    location.search,
    resetForContext,
    session.session_id,
    tenant.namespace,
    tenant.user_id
  ]);

  useEffect(() => {
    if (!enabled) return;
    const params = new URLSearchParams(location.search);
    const urlSession = params.get("session");
    const urlTenant = tenantFromSearchParams(params);
    if (urlSession && (
      urlSession !== session.session_id || !sameTenant(urlTenant, tenant)
    )) {
      return;
    }

    if (!isUrlContextReady(params, session.session_id, tenant)) {
      params.set("session", session.session_id);
      applyTenantSearchParams(params, tenant);
      navigate(`${location.pathname}?${params.toString()}`, { replace: true });
      return;
    }
    rememberSession(session.session_id, tenant);
  }, [
    enabled,
    location.pathname,
    location.search,
    navigate,
    rememberSession,
    session.session_id,
    tenant.namespace,
    tenant.user_id
  ]);

  useEffect(() => {
    if (!enabled) return;
    const params = new URLSearchParams(location.search);
    if (!isUrlContextReady(params, session.session_id, tenant)) return;

    const controller = new AbortController();
    void loadSessionContext({
      sessionId: session.session_id,
      tenant,
      signal: controller.signal,
      api: sessionApi,
      store: {
        hydrateTranscript,
        setSessionState,
        setMessages,
        setLearning,
        addSystemMessage
      }
    });
    return () => controller.abort();
  }, [
    addSystemMessage,
    enabled,
    hydrateTranscript,
    location.search,
    session.session_id,
    setLearning,
    setMessages,
    setSessionState,
    tenant.namespace,
    tenant.user_id
  ]);
}
