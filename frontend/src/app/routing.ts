import { applyTenantSearchParams } from "../tenant";
import type { TenantScope } from "../types";
import { makeSessionId } from "../utils";


export const GITHUB_URL = "https://github.com/pipehui/tech-doc-reader-agent";

export type AppView = "landing" | "studio" | "inspector" | "learner";
export type ExperienceView = Exclude<AppView, "landing">;


export function routeName(pathname: string): AppView {
  const name = pathname.replace(/^\/+/, "").split("/")[0];
  if (name === "studio" || name === "inspector" || name === "learner") {
    return name;
  }
  return "landing";
}


export function experiencePath(
  view: ExperienceView,
  tenant?: Partial<TenantScope>,
  sessionId = makeSessionId(),
  prompt?: string
) {
  const params = new URLSearchParams({ session: sessionId });
  applyTenantSearchParams(params, tenant);
  if (prompt) params.set("prompt", prompt);
  return `/${view}?${params.toString()}`;
}
