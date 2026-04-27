import type { CSSProperties } from "react";
import type { AgentKey } from "./types";

export const agentMeta: Record<AgentKey, { label: string; color: string; soft: string }> = {
  primary: { label: "primary", color: "#4F46E5", soft: "#EEF2FF" },
  parser: { label: "parser", color: "#D97706", soft: "#FEF3C7" },
  relation: { label: "relation", color: "#7C3AED", soft: "#EDE9FE" },
  explanation: { label: "explanation", color: "#059669", soft: "#D1FAE5" },
  examination: { label: "examination", color: "#E11D48", soft: "#FFE4E6" },
  summary: { label: "summary", color: "#475569", soft: "#F1F5F9" }
};

export function normalizeAgent(agent: unknown): AgentKey {
  if (!agent) return "primary";
  const raw = String(agent).trim();
  if (raw === "primary_assistant") return "primary";
  for (const key of Object.keys(agentMeta) as AgentKey[]) {
    if (raw === key || raw.startsWith(`${key}_assistant`) || raw.includes(key)) return key;
  }
  return "primary";
}

export function agentStyle(agent: unknown): CSSProperties {
  return { "--agent-color": agentMeta[normalizeAgent(agent)].color } as CSSProperties;
}
