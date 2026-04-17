import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

export function Layout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const map: Record<string, string> = {
      o: "/",
      r: "/run",
      s: "/sessions",
      c: "/channels",
      l: "/logs",
    };
    let primed = false;
    let primedTimer: number | undefined;

    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (!primed && k === "g") {
        primed = true;
        window.clearTimeout(primedTimer);
        primedTimer = window.setTimeout(() => (primed = false), 900);
        return;
      }
      if (primed && map[k]) {
        primed = false;
        window.clearTimeout(primedTimer);
        window.location.hash = "";
        const base = import.meta.env.BASE_URL.replace(/\/$/, "");
        window.history.pushState({}, "", `${base}${map[k]}`);
        window.dispatchEvent(new PopStateEvent("popstate"));
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="app">
      <Sidebar open={open} onNavigate={() => setOpen(false)} />
      <div className="main">
        <TopBar onMenu={() => setOpen((v) => !v)} />
        <main className="view fade-in">{children}</main>
      </div>
    </div>
  );
}
