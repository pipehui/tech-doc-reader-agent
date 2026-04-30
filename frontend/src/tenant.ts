import type { SessionState, TenantScope } from "./types";

export const DEFAULT_USER_ID = "default";
export const DEFAULT_NAMESPACE = "tech_docs";
const TENANT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;

export function normalizeTenantValue(value: unknown, fallback: string) {
  if (typeof value !== "string") return fallback;
  const text = value.trim();
  if (!text) return fallback;
  return TENANT_ID_PATTERN.test(text) ? text : fallback;
}

export function normalizeTenant(scope?: Partial<TenantScope> | null): TenantScope {
  return {
    user_id: normalizeTenantValue(scope?.user_id, DEFAULT_USER_ID),
    namespace: normalizeTenantValue(scope?.namespace, DEFAULT_NAMESPACE)
  };
}

export function sessionTenant(session: Pick<SessionState, "user_id" | "namespace">): TenantScope {
  return normalizeTenant({
    user_id: session.user_id ?? undefined,
    namespace: session.namespace ?? undefined
  });
}

export function tenantKey(scope?: Partial<TenantScope> | null) {
  const tenant = normalizeTenant(scope);
  return `${tenant.user_id}::${tenant.namespace}`;
}

export function sameTenant(
  left?: Partial<TenantScope> | null,
  right?: Partial<TenantScope> | null
) {
  return tenantKey(left) === tenantKey(right);
}

export function applyTenantSearchParams(
  params: URLSearchParams,
  scope?: Partial<TenantScope> | null
) {
  const tenant = normalizeTenant(scope);
  params.set("user_id", tenant.user_id);
  params.set("namespace", tenant.namespace);
  return params;
}

export function tenantFromSearchParams(params: URLSearchParams): TenantScope {
  return normalizeTenant({
    user_id: params.get("user_id") || undefined,
    namespace: params.get("namespace") || undefined
  });
}
