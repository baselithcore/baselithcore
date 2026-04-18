import { useState } from 'react';
import { Panel } from '../../../components/Panel';
import { Icon, paths } from '../../../lib/icons';
import type { DesktopShared } from '../shared';

export function ShellSection({ shared }: { shared: DesktopShared }) {
  const { policy, canUse, invoke, launcherBinary } = shared;
  const [openAppName, setOpenAppName] = useState('Finder');
  const [shellCmd, setShellCmd] = useState('');
  const [shellCwd, setShellCwd] = useState('');

  return (
    <Panel title="Shell and launcher" tag={policy.allow_shell ? 'enabled' : 'gated'}>
      <div className="desktop-action-card">
        <div className="desktop-action-head">
          <div>
            <strong>macOS app launcher</strong>
            <p className="muted">
              The launcher uses the same shell tool as the agent. It automatically picks the
              exported `open` binary from the allowlist when present.
            </p>
          </div>
          <span className={`badge ${launcherBinary ? 'ok' : 'warn'}`}>
            {launcherBinary ? launcherBinary : 'open not allowlisted'}
          </span>
        </div>

        <div className="inline" style={{ alignItems: 'flex-end' }}>
          <div className="form-row" style={{ flex: 1 }}>
            <label htmlFor="openapp">Application name</label>
            <input
              id="openapp"
              className="input"
              placeholder="Finder"
              value={openAppName}
              onChange={(e) => setOpenAppName(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="btn primary"
            disabled={
              !canUse('baselithbot_shell_run', 'allow_shell') ||
              openAppName.trim().length === 0 ||
              !launcherBinary
            }
            onClick={() =>
              invoke('baselithbot_shell_run', {
                command: `${JSON.stringify(launcherBinary)} -a ${JSON.stringify(openAppName.trim())}`,
              })
            }
          >
            <Icon path={paths.play} size={14} />
            Launch
          </button>
        </div>

        <div className="chip-row" style={{ marginTop: 10 }}>
          {['Finder', 'Safari', 'Terminal', 'System Settings'].map((name) => (
            <button key={name} type="button" className="badge" onClick={() => setOpenAppName(name)}>
              {name}
            </button>
          ))}
        </div>
      </div>

      <div className="desktop-action-card">
        <div className="desktop-action-head">
          <div>
            <strong>Allowlisted shell execution</strong>
            <p className="muted">
              The first token must match the current allowlist exactly. The UI forwards the command
              string and optional cwd exactly as entered.
            </p>
          </div>
          <span className="badge muted">{policy.shell_timeout_seconds}s timeout</span>
        </div>

        <div className="desktop-shell-presets">
          {policy.allowed_shell_commands.slice(0, 6).map((command) => (
            <button
              key={command}
              type="button"
              className="badge"
              onClick={() => setShellCmd(command)}
            >
              {command}
            </button>
          ))}
        </div>

        <div className="form-row">
          <label htmlFor="shellcmd">Command</label>
          <textarea
            id="shellcmd"
            className="textarea mono"
            placeholder="git status"
            value={shellCmd}
            onChange={(e) => setShellCmd(e.target.value)}
            rows={4}
          />
        </div>

        <div className="desktop-form-grid">
          <div className="form-row">
            <label htmlFor="shellcwd">Working directory (optional)</label>
            <input
              id="shellcwd"
              className="input mono"
              placeholder="/tmp"
              value={shellCwd}
              onChange={(e) => setShellCwd(e.target.value)}
            />
          </div>
        </div>

        <div className="inline" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="muted mono" style={{ fontSize: 12 }}>
            {policy.allowed_shell_commands.length === 0
              ? 'No shell tokens currently allowlisted'
              : policy.allowed_shell_commands.join(', ')}
          </span>
          <button
            type="button"
            className="btn primary"
            disabled={
              !canUse('baselithbot_shell_run', 'allow_shell') || shellCmd.trim().length === 0
            }
            onClick={() =>
              invoke('baselithbot_shell_run', {
                command: shellCmd.trim(),
                ...(shellCwd.trim() ? { cwd: shellCwd.trim() } : {}),
              })
            }
          >
            <Icon path={paths.play} size={14} />
            Run
          </button>
        </div>
      </div>
    </Panel>
  );
}
