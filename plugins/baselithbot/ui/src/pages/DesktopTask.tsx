import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { useToasts } from '../components/ToastProvider';
import { api, type DesktopToolInvocation, type DesktopToolPolicy } from '../lib/api';
import { Icon, paths } from '../lib/icons';

interface RunLogEntry {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  result: DesktopToolInvocation['result'];
  ts: number;
}

function statusTone(status: string): 'ok' | 'warn' | 'err' | 'muted' {
  if (status === 'success') return 'ok';
  if (status === 'denied') return 'warn';
  if (status === 'error') return 'err';
  return 'muted';
}

function capabilityChecklist(policy: DesktopToolPolicy): {
  ready: boolean;
  messages: string[];
} {
  const messages: string[] = [];
  if (!policy.enabled) {
    messages.push('Master switch is OFF. Enable Computer Use to dispatch any desktop tool.');
  }
  if (policy.allow_shell && policy.allowed_shell_commands.length === 0) {
    messages.push('Shell capability is armed, but the allowlist is empty. Add first tokens.');
  }
  if (policy.allow_filesystem && !policy.filesystem_root) {
    messages.push('Filesystem is armed without a root — all path resolution will be denied.');
  }
  return { ready: messages.length === 0, messages };
}

export function DesktopTask() {
  const qc = useQueryClient();
  const { push } = useToasts();

  const catalog = useQuery({
    queryKey: ['desktopTools'],
    queryFn: api.desktopTools,
    refetchInterval: 15_000,
  });

  const [openAppName, setOpenAppName] = useState('Finder');
  const [shellCmd, setShellCmd] = useState('');
  const [typeText, setTypeText] = useState('');
  const [pressKey, setPressKey] = useState('');
  const [hotkeyStr, setHotkeyStr] = useState('cmd,space');
  const [mouseX, setMouseX] = useState<number>(0);
  const [mouseY, setMouseY] = useState<number>(0);
  const [mouseButton, setMouseButton] = useState<'left' | 'right' | 'middle'>('left');
  const [mouseClicks, setMouseClicks] = useState<number>(1);
  const [fsPath, setFsPath] = useState('.');
  const [fsWritePath, setFsWritePath] = useState('');
  const [fsWriteContent, setFsWriteContent] = useState('');
  const [runLog, setRunLog] = useState<RunLogEntry[]>([]);

  const policy = catalog.data?.policy;
  const { ready, messages: policyMessages } = useMemo(
    () => (policy ? capabilityChecklist(policy) : { ready: false, messages: [] }),
    [policy],
  );

  const invokeMutation = useMutation({
    mutationFn: ({
      tool,
      args,
    }: {
      tool: string;
      args: Record<string, unknown>;
    }) => api.invokeDesktopTool(tool, args),
    onSuccess: (data, variables) => {
      const entry: RunLogEntry = {
        id: `${Date.now()}-${data.tool}`,
        tool: data.tool,
        args: variables.args,
        result: data.result,
        ts: Date.now(),
      };
      setRunLog((prev) => [entry, ...prev].slice(0, 20));
      const tone = data.result.status === 'success' ? 'success' : 'error';
      push({
        tone,
        title: `${data.tool}: ${data.result.status}`,
        description:
          data.result.status === 'success'
            ? 'Handler returned success.'
            : String(data.result.error ?? 'denied or error'),
      });
      qc.invalidateQueries({ queryKey: ['desktopTools'] });
    },
    onError: (err: unknown, variables) => {
      const message = err instanceof Error ? err.message : String(err);
      setRunLog((prev) =>
        [
          {
            id: `${Date.now()}-${variables.tool}-err`,
            tool: variables.tool,
            args: variables.args,
            result: { status: 'error', error: message },
            ts: Date.now(),
          },
          ...prev,
        ].slice(0, 20),
      );
      push({
        tone: 'error',
        title: 'Desktop tool dispatch failed',
        description: message,
      });
    },
  });

  const invoke = (tool: string, args: Record<string, unknown>) =>
    invokeMutation.mutate({ tool, args });

  const lastEntry = runLog[0] ?? null;
  const lastScreenshot =
    lastEntry && lastEntry.result.status === 'success'
      ? (lastEntry.result.screenshot_base64 as string | undefined)
      : undefined;

  if (catalog.isLoading || !policy) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <PageHeader
          eyebrow="Agent"
          title="Desktop Task"
          description="Invoke Computer Use tools directly on the operator host — screenshots, mouse, keyboard, shell, filesystem."
        />
        <Skeleton height={240} />
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Agent"
        title="Desktop Task"
        description="Invoke Computer Use tools directly on the operator host — screenshots, mouse, keyboard, shell, filesystem. Capability gates enforced by the current policy."
        actions={
          <Link to="/computer-use" className="btn ghost" aria-label="Open Computer Use policy">
            <Icon path={paths.shield} size={14} />
            Policy
          </Link>
        }
      />

      <Panel title="Runtime policy" tag={policy.enabled ? 'armed' : 'locked'}>
        <div className="chip-row" style={{ marginBottom: 12 }}>
          <span className={`badge ${policy.enabled ? 'ok' : 'err'}`}>
            {policy.enabled ? 'master: on' : 'master: off'}
          </span>
          <span className={`badge ${policy.allow_screenshot ? 'ok' : 'muted'}`}>
            screenshots {policy.allow_screenshot ? 'on' : 'off'}
          </span>
          <span className={`badge ${policy.allow_mouse ? 'ok' : 'muted'}`}>
            mouse {policy.allow_mouse ? 'on' : 'off'}
          </span>
          <span className={`badge ${policy.allow_keyboard ? 'ok' : 'muted'}`}>
            keyboard {policy.allow_keyboard ? 'on' : 'off'}
          </span>
          <span className={`badge ${policy.allow_shell ? 'warn' : 'muted'}`}>
            shell {policy.allow_shell ? 'on' : 'off'}
          </span>
          <span className={`badge ${policy.allow_filesystem ? 'warn' : 'muted'}`}>
            fs {policy.allow_filesystem ? 'on' : 'off'}
          </span>
        </div>

        {!ready && (
          <div className="computer-warning-list">
            {policyMessages.map((msg) => (
              <div key={msg} className="computer-warning-item">
                <Icon path={paths.shieldOff} size={14} />
                <span>{msg}</span>
              </div>
            ))}
            <div style={{ marginTop: 8 }}>
              <Link to="/computer-use" className="btn primary">
                <Icon path={paths.shield} size={14} />
                Open Computer Use policy
              </Link>
            </div>
          </div>
        )}
      </Panel>

      <section className="grid grid-split-2-1">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Panel title="Open application" tag="shell · open -a">
            <p className="muted" style={{ marginBottom: 12, fontSize: 13 }}>
              macOS quick launcher. Executes <span className="mono">open -a &lt;app&gt;</span> via
              the shell handler. Add <span className="mono">open</span> to the shell allowlist on
              the Computer Use page first.
            </p>
            <div className="inline" style={{ alignItems: 'flex-end', gap: 10 }}>
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
                  !policy.enabled ||
                  !policy.allow_shell ||
                  openAppName.trim().length === 0 ||
                  invokeMutation.isPending
                }
                onClick={() =>
                  invoke('baselithbot_shell_run', {
                    command: `open -a ${JSON.stringify(openAppName.trim())}`,
                  })
                }
              >
                <Icon path={paths.play} size={14} />
                Launch
              </button>
            </div>
            <div className="chip-row" style={{ marginTop: 10 }}>
              {['Finder', 'Safari', 'Terminal', 'System Settings'].map((name) => (
                <button
                  key={name}
                  type="button"
                  className="badge"
                  onClick={() => setOpenAppName(name)}
                >
                  {name}
                </button>
              ))}
            </div>
          </Panel>

          <Panel title="Screenshot" tag={policy.allow_screenshot ? 'enabled' : 'disabled'}>
            <div className="inline" style={{ gap: 8 }}>
              <button
                type="button"
                className="btn primary"
                disabled={
                  !policy.enabled || !policy.allow_screenshot || invokeMutation.isPending
                }
                onClick={() =>
                  invoke('baselithbot_desktop_screenshot', {
                    monitor: 1,
                    image_format: 'PNG',
                    quality: 80,
                  })
                }
              >
                <Icon path={paths.copy} size={14} />
                Capture
              </button>
              <button
                type="button"
                className="btn ghost"
                disabled={!policy.enabled || invokeMutation.isPending}
                onClick={() => invoke('baselithbot_screen_size', {})}
              >
                Screen size
              </button>
            </div>
          </Panel>

          <Panel title="Shell command" tag={policy.allow_shell ? 'enabled' : 'disabled'}>
            <div className="form-row">
              <label htmlFor="shellcmd">Command (allowlist first-token match)</label>
              <textarea
                id="shellcmd"
                className="textarea mono"
                placeholder="git status"
                value={shellCmd}
                onChange={(e) => setShellCmd(e.target.value)}
                rows={3}
              />
            </div>
            <div className="inline" style={{ justifyContent: 'space-between', marginTop: 8 }}>
              <span className="muted mono" style={{ fontSize: 12 }}>
                allowlist: {policy.allowed_shell_commands.length} entries · timeout{' '}
                {policy.shell_timeout_seconds}s
              </span>
              <button
                type="button"
                className="btn primary"
                disabled={
                  !policy.enabled ||
                  !policy.allow_shell ||
                  shellCmd.trim().length === 0 ||
                  invokeMutation.isPending
                }
                onClick={() =>
                  invoke('baselithbot_shell_run', { command: shellCmd.trim() })
                }
              >
                <Icon path={paths.play} size={14} />
                Run
              </button>
            </div>
          </Panel>

          <Panel title="Mouse" tag={policy.allow_mouse ? 'enabled' : 'disabled'}>
            <div className="inline" style={{ gap: 10, alignItems: 'flex-end' }}>
              <div className="form-row" style={{ flex: '0 0 120px' }}>
                <label htmlFor="mx">X</label>
                <input
                  id="mx"
                  className="input"
                  type="number"
                  value={mouseX}
                  onChange={(e) => setMouseX(Number(e.target.value))}
                />
              </div>
              <div className="form-row" style={{ flex: '0 0 120px' }}>
                <label htmlFor="my">Y</label>
                <input
                  id="my"
                  className="input"
                  type="number"
                  value={mouseY}
                  onChange={(e) => setMouseY(Number(e.target.value))}
                />
              </div>
              <div className="form-row" style={{ flex: '0 0 140px' }}>
                <label htmlFor="mb">Button</label>
                <select
                  id="mb"
                  className="input"
                  value={mouseButton}
                  onChange={(e) =>
                    setMouseButton(e.target.value as 'left' | 'right' | 'middle')
                  }
                >
                  <option value="left">left</option>
                  <option value="right">right</option>
                  <option value="middle">middle</option>
                </select>
              </div>
              <div className="form-row" style={{ flex: '0 0 100px' }}>
                <label htmlFor="mc">Clicks</label>
                <input
                  id="mc"
                  className="input"
                  type="number"
                  min={1}
                  max={5}
                  value={mouseClicks}
                  onChange={(e) => setMouseClicks(Math.max(1, Number(e.target.value) || 1))}
                />
              </div>
            </div>
            <div className="inline" style={{ gap: 8, marginTop: 10 }}>
              <button
                type="button"
                className="btn ghost"
                disabled={!policy.enabled || !policy.allow_mouse || invokeMutation.isPending}
                onClick={() =>
                  invoke('baselithbot_mouse_move', { x: mouseX, y: mouseY, duration: 0.0 })
                }
              >
                Move
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={!policy.enabled || !policy.allow_mouse || invokeMutation.isPending}
                onClick={() =>
                  invoke('baselithbot_mouse_click', {
                    x: mouseX,
                    y: mouseY,
                    button: mouseButton,
                    clicks: mouseClicks,
                  })
                }
              >
                <Icon path={paths.play} size={14} />
                Click
              </button>
            </div>
          </Panel>

          <Panel title="Keyboard" tag={policy.allow_keyboard ? 'enabled' : 'disabled'}>
            <div className="form-row">
              <label htmlFor="ktype">Type text</label>
              <div className="inline" style={{ gap: 8 }}>
                <input
                  id="ktype"
                  className="input"
                  placeholder="hello world"
                  value={typeText}
                  onChange={(e) => setTypeText(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  className="btn primary"
                  disabled={
                    !policy.enabled ||
                    !policy.allow_keyboard ||
                    typeText.length === 0 ||
                    invokeMutation.isPending
                  }
                  onClick={() =>
                    invoke('baselithbot_kbd_type', { text: typeText, interval: 0.0 })
                  }
                >
                  Type
                </button>
              </div>
            </div>
            <div className="form-row">
              <label htmlFor="kpress">Press single key</label>
              <div className="inline" style={{ gap: 8 }}>
                <input
                  id="kpress"
                  className="input"
                  placeholder="enter"
                  value={pressKey}
                  onChange={(e) => setPressKey(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  className="btn"
                  disabled={
                    !policy.enabled ||
                    !policy.allow_keyboard ||
                    pressKey.length === 0 ||
                    invokeMutation.isPending
                  }
                  onClick={() => invoke('baselithbot_kbd_press', { key: pressKey })}
                >
                  Press
                </button>
              </div>
            </div>
            <div className="form-row">
              <label htmlFor="khot">Hotkey (comma separated)</label>
              <div className="inline" style={{ gap: 8 }}>
                <input
                  id="khot"
                  className="input mono"
                  placeholder="cmd,space"
                  value={hotkeyStr}
                  onChange={(e) => setHotkeyStr(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  className="btn"
                  disabled={
                    !policy.enabled ||
                    !policy.allow_keyboard ||
                    hotkeyStr.trim().length === 0 ||
                    invokeMutation.isPending
                  }
                  onClick={() => {
                    const keys = hotkeyStr
                      .split(',')
                      .map((k) => k.trim())
                      .filter(Boolean);
                    if (keys.length === 0) return;
                    invoke('baselithbot_kbd_hotkey', { keys });
                  }}
                >
                  Hotkey
                </button>
              </div>
            </div>
          </Panel>

          <Panel title="Filesystem" tag={policy.allow_filesystem ? 'enabled' : 'disabled'}>
            <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
              Scoped under:{' '}
              <span className="mono">{policy.filesystem_root ?? '— not configured —'}</span> · max
              write: {policy.filesystem_max_bytes.toLocaleString()} bytes
            </p>
            <div className="form-row">
              <label htmlFor="fspath">Path (read / list)</label>
              <div className="inline" style={{ gap: 8 }}>
                <input
                  id="fspath"
                  className="input mono"
                  value={fsPath}
                  onChange={(e) => setFsPath(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  className="btn ghost"
                  disabled={
                    !policy.enabled || !policy.allow_filesystem || invokeMutation.isPending
                  }
                  onClick={() => invoke('baselithbot_fs_list', { path: fsPath })}
                >
                  List
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={
                    !policy.enabled || !policy.allow_filesystem || invokeMutation.isPending
                  }
                  onClick={() => invoke('baselithbot_fs_read', { path: fsPath })}
                >
                  Read
                </button>
              </div>
            </div>
            <div className="form-row">
              <label htmlFor="fswp">Write path</label>
              <input
                id="fswp"
                className="input mono"
                placeholder="notes/out.txt"
                value={fsWritePath}
                onChange={(e) => setFsWritePath(e.target.value)}
              />
            </div>
            <div className="form-row">
              <label htmlFor="fswc">Content (UTF-8)</label>
              <textarea
                id="fswc"
                className="textarea mono"
                rows={4}
                value={fsWriteContent}
                onChange={(e) => setFsWriteContent(e.target.value)}
              />
            </div>
            <div className="inline" style={{ justifyContent: 'flex-end', marginTop: 6 }}>
              <button
                type="button"
                className="btn primary"
                disabled={
                  !policy.enabled ||
                  !policy.allow_filesystem ||
                  fsWritePath.trim().length === 0 ||
                  invokeMutation.isPending
                }
                onClick={() =>
                  invoke('baselithbot_fs_write', {
                    path: fsWritePath,
                    content: fsWriteContent,
                  })
                }
              >
                Write
              </button>
            </div>
          </Panel>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Panel
            title="Last invocation"
            tag={lastEntry ? lastEntry.tool : '—'}
          >
            {!lastEntry ? (
              <EmptyState
                title="No invocations yet"
                description="Trigger a tool on the left. Output and screenshots appear here."
              />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div className="inline">
                  <span className={`badge ${statusTone(lastEntry.result.status)}`}>
                    {lastEntry.result.status}
                  </span>
                  <span className="badge muted mono">{lastEntry.tool}</span>
                </div>
                {lastScreenshot && (
                  <img
                    className="screenshot"
                    alt="Desktop screenshot"
                    src={`data:image/png;base64,${lastScreenshot}`}
                  />
                )}
                <pre
                  className="mono"
                  style={{
                    background: 'var(--surface-1, #0b0f14)',
                    padding: 10,
                    borderRadius: 8,
                    maxHeight: 260,
                    overflow: 'auto',
                    fontSize: 12,
                  }}
                >
                  {JSON.stringify(
                    lastScreenshot
                      ? { ...lastEntry.result, screenshot_base64: '[base64 omitted]' }
                      : lastEntry.result,
                    null,
                    2,
                  )}
                </pre>
              </div>
            )}
          </Panel>

          <Panel title="Recent invocations" tag={String(runLog.length)}>
            {runLog.length === 0 ? (
              <EmptyState
                title="No history"
                description="Recent tool runs appear here (last 20)."
              />
            ) : (
              <div className="stack-list">
                {runLog.map((entry) => (
                  <div key={entry.id} className="select-row">
                    <div className="select-row-head">
                      <span className="badge muted mono">{entry.tool}</span>
                      <span className={`badge ${statusTone(entry.result.status)}`}>
                        {entry.result.status}
                      </span>
                    </div>
                    <div className="muted mono" style={{ fontSize: 11 }}>
                      {new Date(entry.ts).toLocaleTimeString()} ·{' '}
                      {JSON.stringify(entry.args).slice(0, 120)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </section>
    </div>
  );
}
