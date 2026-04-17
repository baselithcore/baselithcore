import { useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { useDashboardEvents } from '../lib/sse';
import { useDashboardOverview } from './DashboardProvider';
import { Icon, paths } from '../lib/icons';

const TITLES: Record<string, string> = {
  '': 'overview',
  run: 'run task',
  sessions: 'sessions',
  channels: 'channels',
  skills: 'skills',
  crons: 'cron',
  nodes: 'nodes',
  agents: 'agents',
  workspaces: 'workspaces',
  metrics: 'metrics',
  canvas: 'canvas',
  logs: 'live logs',
  doctor: 'doctor',
};

interface Props {
  open: boolean;
  onMenu: () => void;
}

export function TopBar({ open, onMenu }: Props) {
  const location = useLocation();
  const sub = useMemo(() => {
    const seg = location.pathname.replace(/^\//, '').split('/')[0];
    return TITLES[seg] ?? seg;
  }, [location.pathname]);

  const { data: overview } = useDashboardOverview();
  const { state } = useDashboardEvents(1);

  const agentState = overview?.agent.state ?? 'uninitialized';
  const tone = toneForState(agentState);

  return (
    <header className="topbar">
      <button
        type="button"
        className="icon-btn"
        aria-label="Toggle menu"
        aria-expanded={open}
        aria-controls="sidebar"
        onClick={onMenu}
      >
        <Icon path={paths.menu} size={18} />
      </button>

      <div className="breadcrumb">
        <span className="crumb-title">Control Plane</span>
        <span className="crumb-sep">/</span>
        <span className="crumb-sub">{sub}</span>
      </div>

      <div className="topbar-spacer" />

      <span className={`pill ${tone}`} title="Agent state">
        <span className="dot" />
        <span>{agentState}</span>
      </span>

      <span className={`pill ${overview?.agent.stealth_enabled ? 'ok' : ''}`} title="Stealth mode">
        <Icon path={overview?.agent.stealth_enabled ? paths.shield : paths.shieldOff} size={12} />
        <span>{overview?.agent.stealth_enabled ? 'stealth on' : 'stealth off'}</span>
      </span>

      <span
        className={`pill ${state === 'open' ? 'ok' : state === 'error' ? 'down' : 'warn'}`}
        title="Event stream"
      >
        <span className="dot" />
        <span>sse {state}</span>
      </span>
    </header>
  );
}

function toneForState(s: string): 'ok' | 'warn' | 'down' {
  if (s === 'ready') return 'ok';
  if (s === 'stopped' || s === 'stopping') return 'down';
  return 'warn';
}
