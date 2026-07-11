import type { TenantScope, TraceEvent } from "../../types";


export function buildTraceExport(
  sessionId: string,
  tenant: TenantScope,
  events: TraceEvent[],
  now: () => Date = () => new Date()
) {
  return {
    session_id: sessionId,
    user_id: tenant.user_id,
    namespace: tenant.namespace,
    events,
    exportedAt: now().toISOString()
  };
}


export function traceExportFilename(
  sessionId: string,
  tenant: TenantScope,
  timestamp = Date.now()
) {
  return `trace_${tenant.user_id}_${tenant.namespace}_${sessionId}_${timestamp}.json`;
}


export function exportTrace(
  sessionId: string,
  tenant: TenantScope,
  events: TraceEvent[]
) {
  const payload = buildTraceExport(sessionId, tenant, events);
  const blob = new Blob(
    [JSON.stringify(payload, null, 2)],
    { type: "application/json" }
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = traceExportFilename(sessionId, tenant);
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
