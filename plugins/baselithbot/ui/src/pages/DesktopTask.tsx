import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { StatCard } from '../components/StatCard';
import { useToasts } from '../components/ToastProvider';
import {
  api,
  type DesktopToolInvocation,
  type DesktopToolPolicy,
  type DesktopToolSpec,
  type RunTaskState,
} from '../lib/api';
import { formatRelative, truncate } from '../lib/format';
import { Icon, paths, type IconName } from '../lib/icons';

type CapabilityKey =
  | 'allow_screenshot'
  | 'allow_mouse'
  | 'allow_keyboard'
  | 'allow_shell'
  | 'allow_filesystem';

interface RunLogEntry {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  result: DesktopToolInvocation['result'];
  ts: number;
}

interface CapabilitySpec {
  key: CapabilityKey;
  approvalKey: string;
  label: string;
  description: string;
  icon: IconName;
  accent: 'teal' | 'cyan' | 'violet' | 'amber' | 'rose';
  toolNames: string[];
}

const CAPABILITIES: CapabilitySpec[] = [
  {
    key: 'allow_screenshot',
    approvalKey: 'screenshot',
    label: 'Screen',
    description: 'Capture frames and probe host geometry.',
    icon: 'copy',
    accent: 'teal',
    toolNames: ['baselithbot_desktop_screenshot', 'baselithbot_screen_size'],
  },
  {
    key: 'allow_mouse',
    approvalKey: 'mouse',
    label: 'Pointer',
    description: 'Absolute move, click, and wheel dispatch.',
    icon: 'activity',
    accent: 'cyan',
    toolNames: ['baselithbot_mouse_move', 'baselithbot_mouse_click', 'baselithbot_mouse_scroll'],
  },
  {
    key: 'allow_keyboard',
    approvalKey: 'keyboard',
    label: 'Keyboard',
    description: 'Type text, press keys, and send hotkeys.',
    icon: 'terminal',
    accent: 'violet',
    toolNames: ['baselithbot_kbd_type', 'baselithbot_kbd_press', 'baselithbot_kbd_hotkey'],
  },
  {
    key: 'allow_shell',
    approvalKey: 'shell',
    label: 'Shell',
    description: 'Allowlisted subprocess execution on the operator host.',
    icon: 'zap',
    accent: 'amber',
    toolNames: ['baselithbot_shell_run'],
  },
  {
    key: 'allow_filesystem',
    approvalKey: 'filesystem',
    label: 'Filesystem',
    description: 'Scoped read, write, and directory enumeration.',
    icon: 'box',
    accent: 'rose',
    toolNames: ['baselithbot_fs_read', 'baselithbot_fs_write', 'baselithbot_fs_list'],
  },
];

const EXPECTED_TOOL_NAMES = CAPABILITIES.flatMap((capability) => capability.toolNames);

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
    messages.push('Master switch is OFF. Every desktop invocation will short-circuit.');
  }
  if (policy.allow_shell && policy.allowed_shell_commands.length === 0) {
    messages.push('Shell is enabled, but the allowlist is empty. Shell runs will still be denied.');
  }
  if (policy.allow_filesystem && !policy.filesystem_root) {
    messages.push(
      'Filesystem access is enabled without a root, so every path resolution will fail.'
    );
  }
  if (policy.require_approval_for.length > 0) {
    messages.push(
      `Operator approval is required for ${policy.require_approval_for.join(', ')} (${policy.approval_timeout_seconds}s timeout).`
    );
  }
  return { ready: messages.length === 0, messages };
}

function capabilityForTool(toolName: string): CapabilitySpec | undefined {
  return CAPABILITIES.find((capability) => capability.toolNames.includes(toolName));
}

function resultMimeType(result: DesktopToolInvocation['result']): string {
  const format = typeof result.format === 'string' ? result.format.toLowerCase() : 'png';
  if (format === 'jpeg' || format === 'jpg') return 'image/jpeg';
  if (format === 'webp') return 'image/webp';
  return 'image/png';
}

function compactArgs(args: Record<string, unknown>): string {
  return truncate(JSON.stringify(args), 120);
}

function exportedToolNames(tools: DesktopToolSpec[]): Set<string> {
  return new Set(tools.map((tool) => tool.name));
}

function requiredFields(spec: DesktopToolSpec): string {
  const required = spec.input_schema.required ?? [];
  if (required.length === 0) return 'No required args';
  if (required.length === 1) return `Requires ${required[0]}`;
  return `Requires ${required.join(', ')}`;
}

function summarizeResult(result: DesktopToolInvocation['result']): string {
  if (typeof result.error === 'string' && result.error.trim()) return result.error;
  if (typeof result.stdout === 'string' && result.stdout.trim()) return truncate(result.stdout, 96);
  if (typeof result.stderr === 'string' && result.stderr.trim()) return truncate(result.stderr, 96);
  if (typeof result.content === 'string' && result.content.trim())
    return truncate(result.content, 96);
  if (Array.isArray(result.entries)) return `${result.entries.length} filesystem entries`;
  if (typeof result.return_code === 'number') return `return code ${result.return_code}`;
  return truncate(JSON.stringify(result), 96);
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
  const [screenshotMonitor, setScreenshotMonitor] = useState(1);
  const [screenshotFormat, setScreenshotFormat] = useState<'PNG' | 'JPEG' | 'WEBP'>('PNG');
  const [screenshotQuality, setScreenshotQuality] = useState(80);
  const [shellCmd, setShellCmd] = useState('');
  const [shellCwd, setShellCwd] = useState('');
  const [typeText, setTypeText] = useState('');
  const [pressKey, setPressKey] = useState('');
  const [hotkeyStr, setHotkeyStr] = useState('cmd,space');
  const [mouseX, setMouseX] = useState(0);
  const [mouseY, setMouseY] = useState(0);
  const [mouseButton, setMouseButton] = useState<'left' | 'right' | 'middle'>('left');
  const [mouseClicks, setMouseClicks] = useState(1);
  const [scrollAmount, setScrollAmount] = useState(-400);
  const [fsPath, setFsPath] = useState('.');
  const [fsWritePath, setFsWritePath] = useState('');
  const [fsWriteContent, setFsWriteContent] = useState('');
  const [runLog, setRunLog] = useState<RunLogEntry[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const [goalText, setGoalText] = useState('');
  const [goalMaxSteps, setGoalMaxSteps] = useState(12);
  const [activeDesktopRunId, setActiveDesktopRunId] = useState<string | null>(null);

  const policy = catalog.data?.policy;
  const tools = catalog.data?.tools ?? [];

  const toolMap = useMemo(() => new Map(tools.map((tool) => [tool.name, tool])), [tools]);
  const toolNames = useMemo(() => exportedToolNames(tools), [tools]);
  const { ready, messages: policyMessages } = useMemo(
    () => (policy ? capabilityChecklist(policy) : { ready: false, messages: [] }),
    [policy]
  );

  const enabledCapabilities = useMemo(
    () => (policy ? CAPABILITIES.filter((capability) => policy[capability.key]) : []),
    [policy]
  );
  const gatedCapabilities = useMemo(
    () =>
      policy
        ? CAPABILITIES.filter((capability) =>
            policy.require_approval_for.includes(capability.approvalKey)
          )
        : [],
    [policy]
  );
  const missingExpectedTools = useMemo(
    () => EXPECTED_TOOL_NAMES.filter((toolName) => !toolNames.has(toolName)),
    [toolNames]
  );

  const selectedEntry = useMemo(() => {
    if (selectedRunId) {
      const selected = runLog.find((entry) => entry.id === selectedRunId);
      if (selected) return selected;
    }
    return runLog[0] ?? null;
  }, [runLog, selectedRunId]);

  const selectedTool = selectedEntry ? toolMap.get(selectedEntry.tool) : undefined;
  const screenshotBase64 =
    selectedEntry && selectedEntry.result.status === 'success'
      ? (selectedEntry.result.screenshot_base64 as string | undefined)
      : undefined;
  const launcherBinary =
    policy?.allowed_shell_commands.find((entry) => entry === 'open' || entry.endsWith('/open')) ??
    null;

  const invokeMutation = useMutation({
    mutationFn: ({ tool, args }: { tool: string; args: Record<string, unknown> }) =>
      api.invokeDesktopTool(tool, args),
    onSuccess: (data, variables) => {
      const entry: RunLogEntry = {
        id: `${Date.now()}-${data.tool}`,
        tool: data.tool,
        args: variables.args,
        result: data.result,
        ts: Date.now(),
      };
      setRunLog((prev) => [entry, ...prev].slice(0, 20));
      setSelectedRunId(entry.id);
      push({
        tone: data.result.status === 'success' ? 'success' : 'error',
        title: `${data.tool}: ${data.result.status}`,
        description:
          data.result.status === 'success'
            ? summarizeResult(data.result)
            : String(data.result.error ?? 'Invocation denied or failed.'),
      });
      qc.invalidateQueries({ queryKey: ['desktopTools'] });
    },
    onError: (err: unknown, variables) => {
      const message = err instanceof Error ? err.message : String(err);
      const entry: RunLogEntry = {
        id: `${Date.now()}-${variables.tool}-err`,
        tool: variables.tool,
        args: variables.args,
        result: { status: 'error', error: message },
        ts: Date.now(),
      };
      setRunLog((prev) => [entry, ...prev].slice(0, 20));
      setSelectedRunId(entry.id);
      push({
        tone: 'error',
        title: 'Desktop tool dispatch failed',
        description: message,
      });
    },
  });

  const invoke = (tool: string, args: Record<string, unknown>) =>
    invokeMutation.mutate({ tool, args });

  const desktopRunDetail = useQuery({
    queryKey: ['desktopTaskById', activeDesktopRunId],
    queryFn: () => api.desktopTaskById(activeDesktopRunId!),
    enabled: !!activeDesktopRunId,
    refetchInterval: (query) => (query.state.data?.run.status === 'running' ? 1_500 : 6_000),
  });
  const activeDesktopRun: RunTaskState | null = desktopRunDetail.data?.run ?? null;

  const desktopTaskMutation = useMutation({
    mutationFn: (payload: { goal: string; max_steps: number }) => api.desktopTaskDispatch(payload),
    onSuccess: (data) => {
      setActiveDesktopRunId(data.run_id);
      push({
        tone: 'success',
        title: 'Desktop task launched',
        description: `run ${data.run_id} dispatched with ${goalMaxSteps} max steps`,
      });
      qc.invalidateQueries({ queryKey: ['desktopTaskById', data.run_id] });
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : String(err);
      push({
        tone: 'error',
        title: 'Desktop task dispatch failed',
        description: message,
      });
    },
  });

  const goalPending = desktopTaskMutation.isPending || activeDesktopRun?.status === 'running';
  const goalDisabled =
    !policy?.enabled ||
    goalText.trim().length === 0 ||
    desktopTaskMutation.isPending ||
    Boolean(activeDesktopRun && activeDesktopRun.status === 'running');

  const canUse = (toolName: string, capabilityKey: CapabilityKey): boolean =>
    Boolean(
      policy &&
      policy.enabled &&
      policy[capabilityKey] &&
      toolMap.has(toolName) &&
      !invokeMutation.isPending
    );

  if (catalog.isLoading) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <PageHeader
          eyebrow="Agent"
          title="Desktop Task"
          description="Direct control surface for the Baselithbot Computer Use plugin."
        />
        <Skeleton height={320} />
      </div>
    );
  }

  if (catalog.isError) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <PageHeader
          eyebrow="Agent"
          title="Desktop Task"
          description="Desktop tool catalog unavailable."
        />
        <Panel>
          <EmptyState
            title="Desktop catalog unavailable"
            description={
              catalog.error instanceof Error
                ? catalog.error.message
                : 'The dashboard could not load the desktop tool surface.'
            }
          />
        </Panel>
      </div>
    );
  }

  if (!policy) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <PageHeader
          eyebrow="Agent"
          title="Desktop Task"
          description="Desktop tool catalog unavailable."
        />
        <Skeleton height={320} />
      </div>
    );
  }

  return (
    <div className="desktop-page">
      <PageHeader
        eyebrow="Agent"
        title="Desktop Task"
        description="Live desktop control surface backed by the Baselithbot plugin catalog. Actions are resolved against the current runtime Computer Use policy on every invocation."
        actions={
          <div className="inline">
            <button
              type="button"
              className="btn ghost"
              onClick={() => catalog.refetch()}
              disabled={catalog.isFetching}
            >
              <Icon path={paths.refresh} size={14} />
              {catalog.isFetching ? 'Refreshing…' : 'Refresh catalog'}
            </button>
            <Link to="/computer-use" className="btn primary" aria-label="Open Computer Use policy">
              <Icon path={paths.shield} size={14} />
              Computer Use policy
            </Link>
          </div>
        }
      />

      <section className="grid grid-cols-4">
        <StatCard
          label="Exported tools"
          value={String(tools.length)}
          sub="resolved from plugin.build_computer_tool_map()"
          iconPath={paths.box}
          accent="teal"
        />
        <StatCard
          label="Capabilities armed"
          value={`${enabledCapabilities.length}/${CAPABILITIES.length}`}
          sub={enabledCapabilities.map((entry) => entry.label).join(', ') || 'none'}
          iconPath={paths.bolt}
          accent="cyan"
        />
        <StatCard
          label="Approval gates"
          value={String(gatedCapabilities.length)}
          sub={
            gatedCapabilities.length > 0
              ? `${policy.approval_timeout_seconds}s timeout`
              : 'operator bypassed'
          }
          iconPath={paths.shield}
          accent="amber"
        />
        <StatCard
          label="Inspector"
          value={selectedEntry ? selectedEntry.result.status : 'idle'}
          sub={selectedEntry ? formatRelative(selectedEntry.ts / 1000) : 'no invocations yet'}
          iconPath={paths.activity}
          accent="violet"
        />
      </section>

      <section className="grid grid-split-2-1">
        <Panel
          title="Natural-language goal"
          tag={activeDesktopRun ? activeDesktopRun.status : 'idle'}
        >
          <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
            Describe an OS-level task in plain language — the vision model plans the exact shell /
            mouse / keyboard / filesystem calls and executes them under the current Computer Use
            policy. Example:{' '}
            <span className="mono">"apri Spotify e avvia la playlist Preferiti"</span>.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (goalDisabled) return;
              desktopTaskMutation.mutate({
                goal: goalText.trim(),
                max_steps: goalMaxSteps,
              });
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
          >
            <div className="form-row">
              <label htmlFor="desk-goal">Goal</label>
              <textarea
                id="desk-goal"
                className="textarea"
                rows={3}
                maxLength={1500}
                value={goalText}
                onChange={(event) => setGoalText(event.target.value)}
                placeholder="apri Spotify e avvia la playlist Preferiti"
              />
            </div>
            <div className="inline" style={{ gap: 10, alignItems: 'flex-end' }}>
              <div className="form-row" style={{ flex: '0 0 140px' }}>
                <label htmlFor="desk-steps">Max steps</label>
                <input
                  id="desk-steps"
                  className="input"
                  type="number"
                  min={1}
                  max={30}
                  value={goalMaxSteps}
                  onChange={(event) =>
                    setGoalMaxSteps(Math.max(1, Math.min(30, Number(event.target.value) || 12)))
                  }
                />
              </div>
              <div style={{ flex: 1 }} />
              <button
                type="button"
                className="btn ghost"
                disabled={goalPending}
                onClick={() => {
                  setGoalText('');
                  setGoalMaxSteps(12);
                  setActiveDesktopRunId(null);
                }}
              >
                Reset
              </button>
              <button type="submit" className="btn primary" disabled={goalDisabled}>
                {goalPending ? (
                  <>
                    <span className="spin">
                      <Icon path={paths.refresh} size={14} />
                    </span>
                    Running…
                  </>
                ) : (
                  <>
                    <Icon path={paths.play} size={14} />
                    Launch desktop agent
                  </>
                )}
              </button>
            </div>
          </form>
          {!policy?.enabled && (
            <div className="computer-warning-list" style={{ marginTop: 12 }}>
              <div className="computer-warning-item">
                <Icon path={paths.shieldOff} size={14} />
                <span>
                  Master switch is OFF — the agent cannot invoke any tool. Enable it on the{' '}
                  <Link to="/computer-use">Computer Use</Link> page first.
                </span>
              </div>
            </div>
          )}
        </Panel>

        <Panel
          title="Agent run"
          tag={
            activeDesktopRun ? `${activeDesktopRun.steps_taken}/${activeDesktopRun.max_steps}` : '—'
          }
        >
          {!activeDesktopRun ? (
            <EmptyState
              title="No active run"
              description="Dispatch a goal on the left to see live Observe → Plan → Act telemetry here."
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="inline">
                <span
                  className={`badge ${
                    activeDesktopRun.status === 'completed'
                      ? 'ok'
                      : activeDesktopRun.status === 'failed'
                        ? 'err'
                        : 'warn'
                  }`}
                >
                  {activeDesktopRun.status}
                </span>
                <span className="badge muted mono">{activeDesktopRun.run_id}</span>
                <span className="badge muted">
                  step {activeDesktopRun.steps_taken} / {activeDesktopRun.max_steps}
                </span>
              </div>
              <div className="info-block" style={{ fontSize: 13, lineHeight: 1.45 }}>
                <strong style={{ display: 'block', marginBottom: 4 }}>Goal</strong>
                {activeDesktopRun.goal}
              </div>
              {activeDesktopRun.last_action && (
                <div className="info-block" style={{ fontSize: 13 }}>
                  <strong style={{ display: 'block', marginBottom: 4 }}>Last action</strong>
                  <span className="mono">{activeDesktopRun.last_action}</span>
                  {activeDesktopRun.last_reasoning && (
                    <div className="muted" style={{ marginTop: 4 }}>
                      {activeDesktopRun.last_reasoning}
                    </div>
                  )}
                </div>
              )}
              {activeDesktopRun.error && <div className="run-error">{activeDesktopRun.error}</div>}
              {activeDesktopRun.last_screenshot_b64 && (
                <img
                  className="screenshot"
                  alt="Agent screenshot"
                  src={`data:image/jpeg;base64,${activeDesktopRun.last_screenshot_b64}`}
                />
              )}
              {activeDesktopRun.history.length > 0 && (
                <div className="trace" style={{ maxHeight: 240, overflow: 'auto' }}>
                  {activeDesktopRun.history.slice(-12).map((line, idx) => (
                    <div key={`${activeDesktopRun.run_id}-h-${idx}`} className="step">
                      <span className="num">#{idx + 1}</span>
                      <span className="text mono" style={{ fontSize: 12 }}>
                        {truncate(line, 180)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </Panel>
      </section>

      <section className="grid grid-split-2-1">
        <Panel className="desktop-hero-panel">
          <div className="desktop-hero">
            <div className="desktop-hero-copy">
              <div className="desktop-hero-badges">
                <span className={`pill ${policy.enabled ? 'ok' : 'down'}`}>
                  <span className="dot" />
                  {policy.enabled ? 'Desktop runtime armed' : 'Desktop runtime locked'}
                </span>
                <span className={`pill ${ready ? 'ok' : 'warn'}`}>
                  <span className="dot" />
                  {ready ? 'Catalog ready' : 'Operator attention needed'}
                </span>
              </div>
              <h2>Runtime policy and plugin export are now surfaced together.</h2>
              <p className="muted">
                This tab no longer assumes a fixed tool surface. It reads the live catalog exported
                by the plugin, shows which capabilities are enabled, and highlights policy gaps
                before dispatching any host action.
              </p>
            </div>

            <div className="desktop-capability-grid">
              {CAPABILITIES.map((capability) => {
                const availableCount = capability.toolNames.filter((toolName) =>
                  toolNames.has(toolName)
                ).length;
                const approvalRequired = policy.require_approval_for.includes(
                  capability.approvalKey
                );
                return (
                  <div
                    key={capability.key}
                    className={`desktop-capability-card ${policy[capability.key] ? 'enabled' : 'disabled'}`}
                  >
                    <div className="desktop-capability-head">
                      <div className="desktop-capability-icon">
                        <Icon path={paths[capability.icon]} size={16} />
                      </div>
                      <div>
                        <strong>{capability.label}</strong>
                        <p>{capability.description}</p>
                      </div>
                    </div>
                    <div className="chip-row">
                      <span className={`badge ${policy[capability.key] ? 'ok' : 'muted'}`}>
                        {policy[capability.key] ? 'enabled' : 'disabled'}
                      </span>
                      <span className="badge muted">{availableCount} tool(s)</span>
                      {approvalRequired && <span className="badge warn">approval</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="desktop-meta-grid">
            <div className="desktop-meta-card">
              <span>Shell allowlist</span>
              <strong>{policy.allowed_shell_commands.length}</strong>
              <p>{policy.allowed_shell_commands.join(', ') || 'No commands allowlisted'}</p>
            </div>
            <div className="desktop-meta-card">
              <span>Filesystem root</span>
              <strong>{policy.filesystem_root ? 'configured' : 'missing'}</strong>
              <p>{policy.filesystem_root ?? 'No filesystem root configured'}</p>
            </div>
            <div className="desktop-meta-card">
              <span>Audit sink</span>
              <strong>{policy.audit_log_path ? 'persisted' : 'volatile'}</strong>
              <p>{policy.audit_log_path ?? 'No audit JSONL path configured'}</p>
            </div>
          </div>

          {!ready && (
            <div className="computer-warning-list" style={{ marginTop: 18 }}>
              {policyMessages.map((message) => (
                <div key={message} className="computer-warning-item">
                  <Icon path={paths.shieldOff} size={14} />
                  <span>{message}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Plugin catalog" tag={`${tools.length} exported`}>
          {missingExpectedTools.length > 0 && (
            <div className="computer-warning-list" style={{ marginBottom: 14 }}>
              {missingExpectedTools.map((toolName) => (
                <div key={toolName} className="computer-warning-item">
                  <Icon path={paths.shieldOff} size={14} />
                  <span>{toolName} is expected by the UI but not exported by the plugin.</span>
                </div>
              ))}
            </div>
          )}

          <div className="desktop-tool-grid">
            {tools.map((tool) => {
              const capability = capabilityForTool(tool.name);
              const enabled = capability ? policy[capability.key] : false;
              const gated = capability
                ? policy.require_approval_for.includes(capability.approvalKey)
                : false;
              return (
                <div key={tool.name} className="desktop-tool-card">
                  <div className="desktop-tool-head">
                    <span className={`badge ${enabled ? 'ok' : 'muted'}`}>
                      {capability?.label ?? 'tool'}
                    </span>
                    {gated && <span className="badge warn">approval</span>}
                  </div>
                  <strong>{tool.name.replace(/^baselithbot_/, '')}</strong>
                  <p>{tool.description}</p>
                  <div className="desktop-tool-meta">
                    <span>{requiredFields(tool)}</span>
                    <span>{Object.keys(tool.input_schema.properties ?? {}).length} arg(s)</span>
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>
      </section>

      <section className="grid grid-split-2-1">
        <div className="desktop-stack">
          <Panel title="Screen and pointer" tag={policy.allow_screenshot ? 'enabled' : 'gated'}>
            <div className="desktop-action-card">
              <div className="desktop-action-head">
                <div>
                  <strong>Capture and geometry probe</strong>
                  <p className="muted">
                    Use the live screenshot tool exported by the plugin. Format and quality are
                    forwarded unchanged to the backend handler.
                  </p>
                </div>
                <div className="chip-row">
                  <span
                    className={`badge ${toolMap.has('baselithbot_desktop_screenshot') ? 'ok' : 'err'}`}
                  >
                    screenshot
                  </span>
                  <span
                    className={`badge ${toolMap.has('baselithbot_screen_size') ? 'ok' : 'err'}`}
                  >
                    screen size
                  </span>
                </div>
              </div>

              <div className="desktop-form-grid">
                <div className="form-row">
                  <label htmlFor="monitor">Monitor</label>
                  <input
                    id="monitor"
                    className="input"
                    type="number"
                    min={1}
                    value={screenshotMonitor}
                    onChange={(e) => setScreenshotMonitor(Math.max(1, Number(e.target.value) || 1))}
                  />
                </div>
                <div className="form-row">
                  <label htmlFor="format">Format</label>
                  <select
                    id="format"
                    className="select"
                    value={screenshotFormat}
                    onChange={(e) => setScreenshotFormat(e.target.value as 'PNG' | 'JPEG' | 'WEBP')}
                  >
                    <option value="PNG">PNG</option>
                    <option value="JPEG">JPEG</option>
                    <option value="WEBP">WEBP</option>
                  </select>
                </div>
                <div className="form-row">
                  <label htmlFor="quality">Quality</label>
                  <input
                    id="quality"
                    className="input"
                    type="number"
                    min={1}
                    max={100}
                    value={screenshotQuality}
                    onChange={(e) =>
                      setScreenshotQuality(Math.min(100, Math.max(1, Number(e.target.value) || 80)))
                    }
                  />
                </div>
              </div>

              <div
                className="inline"
                style={{ justifyContent: 'space-between', alignItems: 'center' }}
              >
                <span className="muted mono" style={{ fontSize: 12 }}>
                  {policy.require_approval_for.includes('screenshot')
                    ? `approval timeout ${policy.approval_timeout_seconds}s`
                    : 'direct execution'}
                </span>
                <div className="inline">
                  <button
                    type="button"
                    className="btn ghost"
                    disabled={!canUse('baselithbot_screen_size', 'allow_screenshot')}
                    onClick={() => invoke('baselithbot_screen_size', {})}
                  >
                    Screen size
                  </button>
                  <button
                    type="button"
                    className="btn primary"
                    disabled={!canUse('baselithbot_desktop_screenshot', 'allow_screenshot')}
                    onClick={() =>
                      invoke('baselithbot_desktop_screenshot', {
                        monitor: screenshotMonitor,
                        image_format: screenshotFormat,
                        quality: screenshotQuality,
                      })
                    }
                  >
                    <Icon path={paths.copy} size={14} />
                    Capture
                  </button>
                </div>
              </div>
            </div>

            <div className="desktop-action-card">
              <div className="desktop-action-head">
                <div>
                  <strong>Pointer controls</strong>
                  <p className="muted">
                    Mouse move, click, and scroll are dispatched as direct tool calls and respect
                    the same approval gate as the agent runtime.
                  </p>
                </div>
                <span className={`badge ${policy.allow_mouse ? 'ok' : 'muted'}`}>
                  mouse {policy.allow_mouse ? 'enabled' : 'disabled'}
                </span>
              </div>

              <div className="desktop-form-grid desktop-form-grid-wide">
                <div className="form-row">
                  <label htmlFor="mx">X</label>
                  <input
                    id="mx"
                    className="input"
                    type="number"
                    value={mouseX}
                    onChange={(e) => setMouseX(Number(e.target.value) || 0)}
                  />
                </div>
                <div className="form-row">
                  <label htmlFor="my">Y</label>
                  <input
                    id="my"
                    className="input"
                    type="number"
                    value={mouseY}
                    onChange={(e) => setMouseY(Number(e.target.value) || 0)}
                  />
                </div>
                <div className="form-row">
                  <label htmlFor="mb">Button</label>
                  <select
                    id="mb"
                    className="select"
                    value={mouseButton}
                    onChange={(e) => setMouseButton(e.target.value as 'left' | 'right' | 'middle')}
                  >
                    <option value="left">left</option>
                    <option value="right">right</option>
                    <option value="middle">middle</option>
                  </select>
                </div>
                <div className="form-row">
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
                <div className="form-row">
                  <label htmlFor="scroll">Scroll amount</label>
                  <input
                    id="scroll"
                    className="input"
                    type="number"
                    value={scrollAmount}
                    onChange={(e) => setScrollAmount(Number(e.target.value) || 0)}
                  />
                </div>
              </div>

              <div className="inline">
                <button
                  type="button"
                  className="btn ghost"
                  disabled={!canUse('baselithbot_mouse_move', 'allow_mouse')}
                  onClick={() =>
                    invoke('baselithbot_mouse_move', { x: mouseX, y: mouseY, duration: 0.0 })
                  }
                >
                  Move
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={!canUse('baselithbot_mouse_click', 'allow_mouse')}
                  onClick={() =>
                    invoke('baselithbot_mouse_click', {
                      x: mouseX,
                      y: mouseY,
                      button: mouseButton,
                      clicks: mouseClicks,
                    })
                  }
                >
                  Click
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={!canUse('baselithbot_mouse_scroll', 'allow_mouse')}
                  onClick={() => invoke('baselithbot_mouse_scroll', { amount: scrollAmount })}
                >
                  Scroll
                </button>
              </div>
            </div>
          </Panel>

          <Panel title="Keyboard" tag={policy.allow_keyboard ? 'enabled' : 'gated'}>
            <div className="desktop-action-card">
              <div className="desktop-action-head">
                <div>
                  <strong>Type, press, and hotkey</strong>
                  <p className="muted">
                    Keyboard actions are wired to the exported tool catalog and respect the same
                    approval flow as pointer actions.
                  </p>
                </div>
                <span className={`badge ${policy.allow_keyboard ? 'ok' : 'muted'}`}>
                  keyboard {policy.allow_keyboard ? 'enabled' : 'disabled'}
                </span>
              </div>

              <div className="form-row">
                <label htmlFor="ktype">Type text</label>
                <div className="inline">
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
                      !canUse('baselithbot_kbd_type', 'allow_keyboard') || typeText.length === 0
                    }
                    onClick={() =>
                      invoke('baselithbot_kbd_type', { text: typeText, interval: 0.0 })
                    }
                  >
                    Type
                  </button>
                </div>
              </div>

              <div className="desktop-form-grid">
                <div className="form-row">
                  <label htmlFor="kpress">Press single key</label>
                  <div className="inline">
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
                        !canUse('baselithbot_kbd_press', 'allow_keyboard') ||
                        pressKey.trim().length === 0
                      }
                      onClick={() => invoke('baselithbot_kbd_press', { key: pressKey.trim() })}
                    >
                      Press
                    </button>
                  </div>
                </div>

                <div className="form-row">
                  <label htmlFor="khot">Hotkey chord</label>
                  <div className="inline">
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
                        !canUse('baselithbot_kbd_hotkey', 'allow_keyboard') ||
                        hotkeyStr.trim().length === 0
                      }
                      onClick={() => {
                        const keys = hotkeyStr
                          .split(',')
                          .map((key) => key.trim())
                          .filter(Boolean);
                        if (keys.length === 0) return;
                        invoke('baselithbot_kbd_hotkey', { keys });
                      }}
                    >
                      Hotkey
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </Panel>

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
            </div>

            <div className="desktop-action-card">
              <div className="desktop-action-head">
                <div>
                  <strong>Allowlisted shell execution</strong>
                  <p className="muted">
                    The first token must match the current allowlist exactly. The UI forwards the
                    command string and optional cwd exactly as entered.
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

              <div
                className="inline"
                style={{ justifyContent: 'space-between', alignItems: 'center' }}
              >
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

          <Panel title="Filesystem" tag={policy.allow_filesystem ? 'enabled' : 'gated'}>
            <div className="desktop-action-card">
              <div className="desktop-action-head">
                <div>
                  <strong>Scoped file access</strong>
                  <p className="muted">
                    Every path is resolved under the configured root. This panel exposes the same
                    read, list, and write tools exported by the plugin.
                  </p>
                </div>
                <span className={`badge ${policy.filesystem_root ? 'ok' : 'warn'}`}>
                  {policy.filesystem_root ?? 'no root'}
                </span>
              </div>

              <p className="muted" style={{ fontSize: 12, marginBottom: 12 }}>
                Max payload {policy.filesystem_max_bytes.toLocaleString()} bytes
              </p>

              <div className="form-row">
                <label htmlFor="fspath">Path (read / list)</label>
                <div className="inline">
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
                    disabled={!canUse('baselithbot_fs_list', 'allow_filesystem')}
                    onClick={() => invoke('baselithbot_fs_list', { path: fsPath })}
                  >
                    List
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={!canUse('baselithbot_fs_read', 'allow_filesystem')}
                    onClick={() => invoke('baselithbot_fs_read', { path: fsPath })}
                  >
                    Read
                  </button>
                </div>
              </div>

              <div className="form-row">
                <label htmlFor="fswritepath">Write path</label>
                <input
                  id="fswritepath"
                  className="input mono"
                  placeholder="notes/out.txt"
                  value={fsWritePath}
                  onChange={(e) => setFsWritePath(e.target.value)}
                />
              </div>

              <div className="form-row">
                <label htmlFor="fswritecontent">Content (UTF-8)</label>
                <textarea
                  id="fswritecontent"
                  className="textarea mono"
                  rows={5}
                  value={fsWriteContent}
                  onChange={(e) => setFsWriteContent(e.target.value)}
                />
              </div>

              <div className="inline" style={{ justifyContent: 'flex-end' }}>
                <button
                  type="button"
                  className="btn primary"
                  disabled={
                    !canUse('baselithbot_fs_write', 'allow_filesystem') ||
                    fsWritePath.trim().length === 0
                  }
                  onClick={() =>
                    invoke('baselithbot_fs_write', {
                      path: fsWritePath.trim(),
                      content: fsWriteContent,
                    })
                  }
                >
                  Write
                </button>
              </div>
            </div>
          </Panel>
        </div>

        <div className="desktop-stack">
          <Panel title="Invocation inspector" tag={selectedEntry ? selectedEntry.tool : 'idle'}>
            {!selectedEntry ? (
              <EmptyState
                title="No invocations yet"
                description="Dispatch a desktop tool to inspect payloads, screenshots, stdout, and filesystem results."
              />
            ) : (
              <div className="desktop-result-stack">
                <div className="desktop-result-head">
                  <div>
                    <div className="inline">
                      <span className={`badge ${statusTone(selectedEntry.result.status)}`}>
                        {selectedEntry.result.status}
                      </span>
                      <span className="badge muted mono">{selectedEntry.tool}</span>
                      {selectedTool && (
                        <span className="badge">{requiredFields(selectedTool)}</span>
                      )}
                    </div>
                    <p className="muted" style={{ margin: '10px 0 0', fontSize: 13 }}>
                      {selectedTool?.description ?? 'Tool metadata unavailable'} ·{' '}
                      {formatRelative(selectedEntry.ts / 1000)}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn ghost"
                    onClick={() => setSelectedRunId(selectedEntry.id)}
                  >
                    <Icon path={paths.externalLink} size={14} />
                    Focused
                  </button>
                </div>

                {screenshotBase64 && (
                  <img
                    className="screenshot"
                    alt="Desktop screenshot"
                    src={`data:${resultMimeType(selectedEntry.result)};base64,${screenshotBase64}`}
                  />
                )}

                <div className="desktop-code-block">
                  <div className="desktop-code-header">args</div>
                  <pre className="mono">{JSON.stringify(selectedEntry.args, null, 2)}</pre>
                </div>

                <ResultBlocks result={selectedEntry.result} />
              </div>
            )}
          </Panel>

          <Panel title="Recent invocations" tag={String(runLog.length)}>
            {runLog.length === 0 ? (
              <EmptyState
                title="No history"
                description="The latest 20 desktop calls will appear here."
              />
            ) : (
              <div className="stack-list">
                {runLog.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    className={`select-row ${selectedEntry?.id === entry.id ? 'active' : ''}`}
                    onClick={() => setSelectedRunId(entry.id)}
                  >
                    <div className="select-row-head">
                      <span className="badge muted mono">{entry.tool}</span>
                      <span className={`badge ${statusTone(entry.result.status)}`}>
                        {entry.result.status}
                      </span>
                    </div>
                    <div className="muted mono" style={{ fontSize: 11 }}>
                      {new Date(entry.ts).toLocaleTimeString()} · {compactArgs(entry.args)}
                    </div>
                    <div className="desktop-history-summary">{summarizeResult(entry.result)}</div>
                  </button>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </section>
    </div>
  );
}

function ResultBlocks({ result }: { result: DesktopToolInvocation['result'] }) {
  const stdout = typeof result.stdout === 'string' ? result.stdout : null;
  const stderr = typeof result.stderr === 'string' ? result.stderr : null;
  const content = typeof result.content === 'string' ? result.content : null;
  const entries = Array.isArray(result.entries)
    ? (result.entries as Array<{ name?: string; is_dir?: boolean; size?: number | null }>)
    : null;
  const sanitized = screenshotless(result);

  return (
    <>
      {stdout && (
        <div className="desktop-code-block">
          <div className="desktop-code-header">stdout</div>
          <pre className="mono">{stdout}</pre>
        </div>
      )}
      {stderr && (
        <div className="desktop-code-block">
          <div className="desktop-code-header">stderr</div>
          <pre className="mono">{stderr}</pre>
        </div>
      )}
      {content && (
        <div className="desktop-code-block">
          <div className="desktop-code-header">content</div>
          <pre className="mono">{content}</pre>
        </div>
      )}
      {entries && (
        <div className="desktop-code-block">
          <div className="desktop-code-header">entries</div>
          <div className="desktop-entry-list">
            {entries.slice(0, 20).map((entry, index) => (
              <div key={`${entry.name ?? 'entry'}-${index}`} className="desktop-entry-row">
                <span className={`badge ${entry.is_dir ? 'ok' : 'muted'}`}>
                  {entry.is_dir ? 'dir' : 'file'}
                </span>
                <span className="mono">{entry.name ?? 'unnamed'}</span>
                {typeof entry.size === 'number' && (
                  <span className="muted mono">{entry.size.toLocaleString()} B</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="desktop-code-block">
        <div className="desktop-code-header">result</div>
        <pre className="mono">{JSON.stringify(sanitized, null, 2)}</pre>
      </div>
    </>
  );
}

function screenshotless(result: DesktopToolInvocation['result']): DesktopToolInvocation['result'] {
  if (!('screenshot_base64' in result)) return result;
  return {
    ...result,
    screenshot_base64: '[base64 omitted]',
  };
}
