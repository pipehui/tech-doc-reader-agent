import type {
  HistoryItem,
  HistoryResponse,
  LearningOverview,
  LearningRecord,
  MessageRole,
  SessionState
} from "../../types";


type JsonObject = Record<string, unknown>;


function objectValue(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as JsonObject;
}


function stringValue(value: unknown, label: string) {
  if (typeof value !== "string") throw new Error(`${label} must be a string`);
  return value;
}


function nullableString(value: unknown, label: string) {
  if (value === null) return null;
  return stringValue(value, label);
}


function optionalNullableString(record: JsonObject, key: string) {
  if (!(key in record)) return undefined;
  return nullableString(record[key], key);
}


function booleanValue(value: unknown, label: string) {
  if (typeof value !== "boolean") throw new Error(`${label} must be a boolean`);
  return value;
}


function numberValue(value: unknown, label: string) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}


function integerValue(value: unknown, label: string) {
  const number = numberValue(value, label);
  if (!Number.isInteger(number)) throw new Error(`${label} must be an integer`);
  return number;
}


function arrayValue(value: unknown, label: string) {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}


function messageRole(value: unknown): MessageRole {
  const role = stringValue(value, "role");
  if (role !== "user" && role !== "assistant" && role !== "system" && role !== "tool") {
    throw new Error(`role has unsupported value ${role}`);
  }
  return role;
}


function decodeHistoryItem(payload: unknown): HistoryItem {
  const item = objectValue(payload, "history message");
  return {
    id: optionalNullableString(item, "id"),
    role: messageRole(item.role),
    kind: stringValue(item.kind, "kind"),
    content: stringValue(item.content, "content"),
    name: optionalNullableString(item, "name"),
    tool_call_id: optionalNullableString(item, "tool_call_id")
  };
}


export function decodeSessionState(payload: unknown): SessionState {
  const state = objectValue(payload, "session state");
  return {
    session_id: stringValue(state.session_id, "session_id"),
    user_id: nullableString(state.user_id, "user_id"),
    namespace: nullableString(state.namespace, "namespace"),
    exists: booleanValue(state.exists, "exists"),
    pending_interrupt: booleanValue(
      state.pending_interrupt,
      "pending_interrupt"
    ),
    learning_target: nullableString(state.learning_target, "learning_target"),
    message_count: integerValue(state.message_count, "message_count"),
    current_agent: nullableString(state.current_agent, "current_agent"),
    workflow_plan: arrayValue(state.workflow_plan, "workflow_plan")
      .map((item, index) => stringValue(item, `workflow_plan[${index}]`)),
    plan_index: integerValue(state.plan_index, "plan_index"),
    ...(state.budget_usage === undefined || state.budget_usage === null
      ? {}
      : { budget_usage: objectValue(state.budget_usage, "budget_usage") })
  };
}


export function decodeHistoryResponse(payload: unknown): HistoryResponse {
  const history = objectValue(payload, "session history");
  return {
    session_id: stringValue(history.session_id, "session_id"),
    user_id: optionalNullableString(history, "user_id"),
    namespace: optionalNullableString(history, "namespace"),
    learning_target: nullableString(history.learning_target, "learning_target"),
    pending_interrupt: booleanValue(
      history.pending_interrupt,
      "pending_interrupt"
    ),
    message_count: integerValue(history.message_count, "message_count"),
    messages: arrayValue(history.messages, "messages").map(decodeHistoryItem)
  };
}


function decodeLearningRecord(payload: unknown): LearningRecord {
  const record = objectValue(payload, "learning record");
  return {
    knowledge: stringValue(record.knowledge, "knowledge"),
    timestamp: stringValue(record.timestamp, "timestamp"),
    score: numberValue(record.score, "score"),
    reviewtimes: integerValue(record.reviewtimes, "reviewtimes"),
    user_id: optionalNullableString(record, "user_id"),
    namespace: optionalNullableString(record, "namespace")
  };
}


export function decodeLearningOverview(payload: unknown): LearningOverview {
  const overview = objectValue(payload, "learning overview");
  return {
    user_id: optionalNullableString(overview, "user_id"),
    namespace: optionalNullableString(overview, "namespace"),
    total: integerValue(overview.total, "total"),
    average_score: numberValue(overview.average_score, "average_score"),
    needs_review_count: integerValue(
      overview.needs_review_count,
      "needs_review_count"
    ),
    records: arrayValue(overview.records, "records").map(decodeLearningRecord)
  };
}
