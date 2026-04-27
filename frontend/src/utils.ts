export function uid() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

export function makeSessionId() {
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  return `doc-${day}-${Math.random().toString(16).slice(2, 8)}`;
}

export function pretty(value: unknown) {
  if (value === undefined || value === null || value === "") return "(empty)";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

export function formatTime(value?: string) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function relativeTime(value?: string) {
  const date = value ? new Date(String(value).replace(" ", "T")) : null;
  if (!date || Number.isNaN(date.getTime())) return "时间未知";
  const days = Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000));
  if (days === 0) return "今天";
  if (days < 31) return `${days} 天前`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} 个月前`;
  return `${Math.floor(months / 12)} 年前`;
}

export function daysSince(value?: string) {
  const date = value ? new Date(String(value).replace(" ", "T")) : null;
  if (!date || Number.isNaN(date.getTime())) return 999;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000));
}

export function scoreTone(score: number) {
  if (score < 0.6) return "#E11D48";
  if (score < 0.8) return "#D97706";
  return "#16A34A";
}
