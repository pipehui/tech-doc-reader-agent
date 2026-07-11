import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { experiencePath, type ExperienceView } from "../../app/routing";
import { useAppStore } from "../../store";
import {
  applyTenantSearchParams,
  normalizeTenant,
  sessionTenant
} from "../../tenant";
import type { TenantScope } from "../../types";
import { makeSessionId } from "../../utils";


export function sessionSwitchSearch(
  currentSearch: string,
  sessionId: string,
  tenant: TenantScope
) {
  const params = new URLSearchParams(currentSearch);
  params.set("session", sessionId);
  applyTenantSearchParams(params, tenant);
  params.delete("prompt");
  return params.toString();
}


export function tenantSwitchContext(
  currentSearch: string,
  sessionId: string,
  userId: string,
  namespace: string
) {
  const tenant = normalizeTenant({ user_id: userId, namespace });
  return {
    tenant,
    search: sessionSwitchSearch(currentSearch, sessionId, tenant)
  };
}


export function useSessionControls(isLanding: boolean) {
  const navigate = useNavigate();
  const location = useLocation();
  const session = useAppStore((state) => state.session);
  const resetForContext = useAppStore((state) => state.resetForContext);
  const tenant = useMemo(
    () => sessionTenant(session),
    [session.user_id, session.namespace]
  );
  const [draft, setDraft] = useState(session.session_id);
  const [userDraft, setUserDraft] = useState(tenant.user_id);
  const [namespaceDraft, setNamespaceDraft] = useState(tenant.namespace);

  useEffect(() => setDraft(session.session_id), [session.session_id]);
  useEffect(() => setUserDraft(tenant.user_id), [tenant.user_id]);
  useEffect(() => setNamespaceDraft(tenant.namespace), [tenant.namespace]);

  function go(next: ExperienceView) {
    const nextSession = isLanding ? makeSessionId() : session.session_id;
    navigate(experiencePath(next, tenant, nextSession));
  }

  function switchSession(nextSession: string) {
    const id = nextSession.trim();
    if (!id) return;
    const search = sessionSwitchSearch(location.search, id, tenant);
    resetForContext(id, tenant);
    navigate(`${location.pathname}?${search}`, { replace: true });
  }

  function switchTenant(nextUserId: string, nextNamespace: string) {
    const next = tenantSwitchContext(
      location.search,
      session.session_id,
      nextUserId,
      nextNamespace
    );
    resetForContext(session.session_id, next.tenant);
    navigate(`${location.pathname}?${next.search}`, { replace: true });
  }

  return {
    session,
    draft,
    setDraft,
    userDraft,
    setUserDraft,
    namespaceDraft,
    setNamespaceDraft,
    go,
    switchSession,
    switchTenant
  };
}
