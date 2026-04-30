import { normalizeTenant } from "./tenant";
import type { HistoryResponse, LearningOverview, SessionState, TenantScope } from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

function buildTenantPath(path: string, tenant?: Partial<TenantScope>) {
  const [pathname, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  const resolved = normalizeTenant(tenant);
  params.set("user_id", resolved.user_id);
  params.set("namespace", resolved.namespace);
  const search = params.toString();
  return `${API_BASE}${pathname}${search ? `?${search}` : ""}`;
}

export function tenantHeaders(tenant?: Partial<TenantScope>) {
  const resolved = normalizeTenant(tenant);
  return {
    "x-user-id": resolved.user_id,
    "x-namespace": resolved.namespace
  };
}

export async function fetchJson<T>(path: string, tenant?: Partial<TenantScope>): Promise<T> {
  const response = await fetch(buildTenantPath(path, tenant), {
    headers: {
      Accept: "application/json",
      ...tenantHeaders(tenant)
    }
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function getSessionState(sessionId: string, tenant?: Partial<TenantScope>) {
  return fetchJson<SessionState>(`/sessions/${encodeURIComponent(sessionId)}/state`, tenant);
}

export function getSessionHistory(sessionId: string, tenant?: Partial<TenantScope>) {
  return fetchJson<HistoryResponse>(`/sessions/${encodeURIComponent(sessionId)}/history?include_tools=true`, tenant);
}

export function getLearningOverview(tenant?: Partial<TenantScope>) {
  return fetchJson<LearningOverview>("/learning/overview", tenant);
}
