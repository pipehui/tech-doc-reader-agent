import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  BookOpen,
  Copy,
  GitBranch,
  Home,
  Layers,
  Moon,
  Plus,
  Sun
} from "lucide-react";
import { useAppStore } from "../store";
import {
  applyTenantSearchParams,
  normalizeTenant,
  sessionTenant
} from "../tenant";
import { makeSessionId } from "../utils";
import {
  experiencePath,
  GITHUB_URL,
  type AppView,
  type ExperienceView
} from "./routing";


export function Topbar({ view }: { view: AppView }) {
  const navigate = useNavigate();
  const location = useLocation();
  const isLanding = view === "landing";
  const session = useAppStore((state) => state.session);
  const running = useAppStore((state) => state.running);
  const runLabel = useAppStore((state) => state.runLabel);
  const error = useAppStore((state) => state.error);
  const theme = useAppStore((state) => state.theme);
  const setTheme = useAppStore((state) => state.setTheme);
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
    if (!nextSession.trim()) return;
    const id = nextSession.trim();
    const params = new URLSearchParams(location.search);
    params.set("session", id);
    applyTenantSearchParams(params, tenant);
    params.delete("prompt");
    resetForContext(id, tenant);
    navigate(`${location.pathname}?${params.toString()}`, { replace: true });
  }

  function switchTenant(nextUserId: string, nextNamespace: string) {
    const nextTenant = normalizeTenant({
      user_id: nextUserId,
      namespace: nextNamespace
    });
    const params = new URLSearchParams(location.search);
    params.set("session", session.session_id);
    applyTenantSearchParams(params, nextTenant);
    params.delete("prompt");
    resetForContext(session.session_id, nextTenant);
    navigate(`${location.pathname}?${params.toString()}`, { replace: true });
  }

  return (
    <header className={`topbar ${isLanding ? "landing-topbar" : ""}`}>
      <button
        className="brand brand-button"
        type="button"
        onClick={() => navigate("/")}
        title="回到首页"
      >
        <div className="brand-mark">TD</div>
        <div>
          <p className="eyebrow">LangGraph Agent</p>
          <h1 className="brand-title">技术文档研读助手</h1>
        </div>
      </button>

      <nav className="view-switcher">
        {([
          ["studio", "Studio", Layers],
          ["inspector", "Inspector", Activity],
          ["learner", "Learner", BookOpen]
        ] as const).map(([key, label, Icon]) => (
          <button
            key={key}
            className={`view-tab ${!isLanding && view === key ? "active" : ""}`}
            type="button"
            onClick={() => go(key)}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </nav>

      <div className="topbar-actions">
        {!isLanding && (
          <>
            <label className="session-control">
              <span>Session</span>
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={() => switchSession(draft)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                }}
              />
            </label>
            <label className="session-control tenant-control">
              <span>User</span>
              <input
                value={userDraft}
                onChange={(event) => setUserDraft(event.target.value)}
                onBlur={() => switchTenant(userDraft, namespaceDraft)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                }}
              />
            </label>
            <label className="session-control tenant-control">
              <span>Namespace</span>
              <input
                value={namespaceDraft}
                onChange={(event) => setNamespaceDraft(event.target.value)}
                onBlur={() => switchTenant(userDraft, namespaceDraft)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                }}
              />
            </label>
            <button
              className="icon-button"
              type="button"
              title="复制 session id"
              onClick={() => navigator.clipboard.writeText(session.session_id)}
            >
              <Copy size={16} />
            </button>
            <button
              className="icon-button"
              type="button"
              title="新建会话"
              onClick={() => switchSession(makeSessionId())}
            >
              <Plus size={16} />
            </button>
          </>
        )}
        <button
          className="icon-button"
          type="button"
          title="切换主题"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        >
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
        <a
          className={`github-link ${isLanding ? "prominent" : ""}`}
          href={GITHUB_URL}
          target="_blank"
          rel="noreferrer"
        >
          <GitBranch size={16} />
          GitHub
        </a>
        {!isLanding && (
          <button
            className="icon-button"
            type="button"
            title="回到首页"
            onClick={() => navigate("/")}
          >
            <Home size={16} />
          </button>
        )}
        {!isLanding && (
          <span className={`status-pill ${error ? "error" : running ? "running" : ""}`}>
            {error ? "错误" : runLabel}
          </span>
        )}
      </div>
    </header>
  );
}
