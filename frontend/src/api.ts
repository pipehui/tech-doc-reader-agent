import type { HistoryResponse, LearningOverview, SessionState } from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function getSessionState(sessionId: string) {
  return fetchJson<SessionState>(`/sessions/${encodeURIComponent(sessionId)}/state`);
}

export function getSessionHistory(sessionId: string) {
  return fetchJson<HistoryResponse>(`/sessions/${encodeURIComponent(sessionId)}/history?include_tools=true`);
}

export function getLearningOverview() {
  return fetchJson<LearningOverview>("/learning/overview");
}
