import { useEffect, useRef } from 'react';
import { NavLink } from 'react-router-dom';
import { Icon, paths } from '../lib/icons';

interface NavItem {
  to: string;
  label: string;
  icon: keyof typeof paths;
  hint?: string;
}

const NAV: NavItem[] = [
  { to: '/', label: 'Overview', icon: 'activity', hint: 'G O' },
  { to: '/run', label: 'Run Task', icon: 'play', hint: 'G R' },
  { to: '/sessions', label: 'Sessions', icon: 'messages', hint: 'G S' },
  { to: '/channels', label: 'Channels', icon: 'cable', hint: 'G C' },
  { to: '/skills', label: 'Skills', icon: 'box' },
  { to: '/crons', label: 'Cron', icon: 'clock' },
  { to: '/nodes', label: 'Nodes', icon: 'waypoints' },
  { to: '/agents', label: 'Agents', icon: 'bot' },
  { to: '/workspaces', label: 'Workspaces', icon: 'terminal' },
  { to: '/metrics', label: 'Metrics', icon: 'sparkles' },
  { to: '/canvas', label: 'Canvas', icon: 'copy' },
  { to: '/logs', label: 'Live Logs', icon: 'radar', hint: 'G L' },
  { to: '/doctor', label: 'Doctor', icon: 'heart' },
];

interface Props {
  open: boolean;
  onNavigate: () => void;
  onClose: () => void;
}

export function Sidebar({ open, onNavigate, onClose }: Props) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (open) {
      ref.current?.focus();
    }
  }, [open]);

  return (
    <aside
      ref={ref}
      className={`sidebar ${open ? 'open' : ''}`}
      id="sidebar"
      tabIndex={-1}
      aria-label="Primary navigation"
    >
      <div className="sidebar-head">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <Icon path={paths.bot} size={18} />
          </div>
          <div className="brand-text">
            <strong>Baselithbot</strong>
            <span>control plane</span>
          </div>
        </div>
        <button
          type="button"
          className="sidebar-close"
          aria-label="Close navigation"
          onClick={onClose}
        >
          <Icon path={paths.x} size={16} />
        </button>
      </div>

      <nav className="nav" aria-label="Primary">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            onClick={onNavigate}
            className={({ isActive }) => (isActive ? 'active' : undefined)}
          >
            <Icon path={paths[item.icon]} size={16} />
            <span>{item.label}</span>
            {item.hint && <span className="nav-hint">{item.hint}</span>}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-foot">
        <div className="sidebar-hint">
          <span className="dot" style={{ background: 'var(--accent-teal)' }} aria-hidden />
          <span className="mono">/api/baselithbot/dash</span>
        </div>
        <div className="sidebar-hint">
          <span className="dot" style={{ background: 'var(--accent-violet)' }} aria-hidden />
          <span>plugins/baselithbot/README.md</span>
        </div>
      </div>
    </aside>
  );
}
