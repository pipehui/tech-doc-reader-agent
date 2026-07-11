import { useEffect } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation
} from "react-router-dom";
import { useAppStore } from "../store";
import { Inspector } from "../features/inspector/Inspector";
import { Landing } from "../features/landing/Landing";
import { Learner } from "../features/learner/Learner";
import { useSessionBootstrap } from "../features/session/useSessionBootstrap";
import { Studio } from "../features/studio/Studio";
import { routeName } from "./routing";
import { Topbar } from "./Topbar";


export function AppRouter() {
  const location = useLocation();
  const view = routeName(location.pathname);
  const isLanding = view === "landing";
  const theme = useAppStore((state) => state.theme);
  useSessionBootstrap(!isLanding);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <div className={`app-shell ${isLanding ? "landing-shell" : ""}`}>
      <Topbar view={view} />
      <main className={`view-frame ${isLanding ? "landing-frame" : ""}`}>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/studio" element={<Studio />} />
          <Route path="/inspector" element={<Inspector />} />
          <Route path="/learner" element={<Learner />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <div className="toast-host" aria-live="polite" />
    </div>
  );
}
