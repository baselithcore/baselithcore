import { useEffect, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { ConfirmProvider } from './ConfirmProvider';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { ToastProvider } from './ToastProvider';

export function Layout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const closeMenu = () => setOpen(false);

  useEffect(() => {
    closeMenu();
  }, [location.pathname]);

  useEffect(() => {
    document.body.classList.toggle('app-menu-open', open);
    return () => document.body.classList.remove('app-menu-open');
  }, [open]);

  useEffect(() => {
    const map: Record<string, string> = {
      o: '/',
      r: '/run',
      s: '/sessions',
      c: '/channels',
      l: '/logs',
      a: '/agents',
      w: '/workspaces',
      m: '/metrics',
    };
    let primed = false;
    let primedTimer: number | undefined;

    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (!primed && k === 'g') {
        primed = true;
        window.clearTimeout(primedTimer);
        primedTimer = window.setTimeout(() => (primed = false), 900);
        return;
      }
      if (primed && map[k]) {
        primed = false;
        window.clearTimeout(primedTimer);
        window.location.hash = '';
        const base = import.meta.env.BASE_URL.replace(/\/$/, '');
        window.history.pushState({}, '', `${base}${map[k]}`);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!open) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeMenu();
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <ConfirmProvider>
      <ToastProvider>
        <div className="app">
          <button
            type="button"
            className={`sidebar-scrim ${open ? 'visible' : ''}`}
            aria-label="Close navigation"
            aria-hidden={!open}
            tabIndex={open ? 0 : -1}
            onClick={closeMenu}
          />
          <Sidebar open={open} onNavigate={closeMenu} onClose={closeMenu} />
          <div className="main">
            <TopBar open={open} onMenu={() => setOpen((v) => !v)} />
            <main className="view fade-in">{children}</main>
          </div>
        </div>
      </ToastProvider>
    </ConfirmProvider>
  );
}
