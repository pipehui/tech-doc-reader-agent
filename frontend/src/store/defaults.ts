import { INSPECTOR_EVENT_TYPES } from "../sseContract";
import { normalizeTenant } from "../tenant";
import type { LearningOverview, SessionState, TenantScope } from "../types";


export const EVENT_TYPES = [...INSPECTOR_EVENT_TYPES];


export function createInitialSession(
  sessionId: string,
  tenant?: Partial<TenantScope>
): SessionState {
  const resolved = normalizeTenant(tenant);
  return {
    session_id: sessionId,
    user_id: resolved.user_id,
    namespace: resolved.namespace,
    exists: false,
    pending_interrupt: false,
    learning_target: null,
    message_count: 0,
    current_agent: "primary",
    workflow_plan: [],
    plan_index: 0
  };
}


export function createInitialLearning(): LearningOverview {
  return {
    total: 0,
    average_score: 0,
    needs_review_count: 0,
    records: []
  };
}
