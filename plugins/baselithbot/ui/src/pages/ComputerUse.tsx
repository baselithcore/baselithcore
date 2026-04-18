import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useConfirm } from '../components/ConfirmProvider';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { StatCard } from '../components/StatCard';
import { useToasts } from '../components/ToastProvider';
import { api, type ComputerUseConfig } from '../lib/api';
import { formatNumber } from '../lib/format';
import { Icon, paths, type IconName } from '../lib/icons';

type CapabilityKey =
  | 'allow_screenshot'
  | 'allow_mouse'
  | 'allow_keyboard'
  | 'allow_shell'
  | 'allow_filesystem';

type CapabilitySpec = {
  key: CapabilityKey;
  label: string;
  description: string;
  icon: IconName;
  accent: 'teal' | 'cyan' | 'violet' | 'amber' | 'rose';
  danger?: boolean;
  tools: string[];
};

const CAPABILITY_FIELDS: CapabilitySpec[] = [
  {
    key: 'allow_screenshot',
    label: 'Screenshots',
    description: 'Desktop capture and geometry probes for screen-state awareness.',
    icon: 'copy',
    accent: 'teal',
    tools: ['baselithbot_desktop_screenshot', 'baselithbot_screen_size'],
  },
  {
    key: 'allow_mouse',
    label: 'Mouse control',
    description: 'Absolute move, click, and wheel events on the operator machine.',
    icon: 'activity',
    accent: 'cyan',
    tools: ['baselithbot_mouse_move', 'baselithbot_mouse_click', 'baselithbot_mouse_scroll'],
  },
  {
    key: 'allow_keyboard',
    label: 'Keyboard control',
    description: 'Typing, single-key dispatch, and hotkey chords.',
    icon: 'terminal',
    accent: 'violet',
    tools: ['baselithbot_kbd_type', 'baselithbot_kbd_press', 'baselithbot_kbd_hotkey'],
  },
  {
    key: 'allow_shell',
    label: 'Shell execution',
    description: 'Allowlisted subprocess calls and process management.',
    icon: 'zap',
    accent: 'amber',
    danger: true,
    tools: ['baselithbot_shell_run', 'baselithbot_process_list', 'baselithbot_process_kill'],
  },
  {
    key: 'allow_filesystem',
    label: 'Filesystem scope',
    description: 'Read, write, patch, and enumerate content under a single root.',
    icon: 'box',
    accent: 'rose',
    danger: true,
    tools: [
      'baselithbot_fs_read',
      'baselithbot_fs_write',
      'baselithbot_fs_list',
      'baselithbot_code_diff_apply',
      'baselithbot_code_line_edit',
      'baselithbot_code_search_replace',
      'baselithbot_code_multi_file_write',
    ],
  },
];

function normalizeAllowlistDraft(draft: string): string[] {
  return draft
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function sameConfig(left: ComputerUseConfig, right: ComputerUseConfig): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function summariseAllowlist(entries: string[]): string {
  if (entries.length === 0) return 'No commands allowlisted';
  if (entries.length === 1) return entries[0];
  if (entries.length === 2) return `${entries[0]} and ${entries[1]}`;
  return `${entries[0]}, ${entries[1]}, +${entries.length - 2} more`;
}

function capabilityTone(accent: CapabilitySpec['accent']): 'ok' | 'warn' | 'err' | 'muted' {
  if (accent === 'teal' || accent === 'cyan' || accent === 'violet') return 'ok';
  if (accent === 'amber') return 'warn';
  return 'err';
}

export function ComputerUse() {
  const qc = useQueryClient();
  const { push } = useToasts();
  const confirm = useConfirm();

  const { data, isLoading } = useQuery({
    queryKey: ['computer-use'],
    queryFn: api.computerUse,
    refetchInterval: 15_000,
  });

  const [form, setForm] = useState<ComputerUseConfig | null>(null);
  const [allowlistDraft, setAllowlistDraft] = useState('');

  const normalizedAllowlist = useMemo(
    () => normalizeAllowlistDraft(allowlistDraft),
    [allowlistDraft]
  );

  const draftConfig = useMemo(
    () => (form ? { ...form, allowed_shell_commands: normalizedAllowlist } : null),
    [form, normalizedAllowlist]
  );

  const isDirty = useMemo(
    () => Boolean(data && draftConfig && !sameConfig(draftConfig, data.current)),
    [data, draftConfig]
  );

  const syncFromSource = (next: ComputerUseConfig) => {
    setForm(next);
    setAllowlistDraft(next.allowed_shell_commands.join('\n'));
  };

  useEffect(() => {
    if (!data) return;
    if (!draftConfig) {
      syncFromSource(data.current);
      return;
    }
    if (!isDirty && !sameConfig(draftConfig, data.current)) {
      syncFromSource(data.current);
    }
  }, [data, draftConfig, isDirty]);

  const mutation = useMutation({
    mutationFn: (config: ComputerUseConfig) => api.updateComputerUse(config),
    onSuccess: (res) => {
      syncFromSource(res.current);
      qc.invalidateQueries({ queryKey: ['computer-use'] });
      qc.invalidateQueries({ queryKey: ['overview'] });
      push({
        tone: 'success',
        title: 'Computer Use policy saved',
        description: `Overlay updated. Agent runtime will rebuild with the new guardrails.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Save failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  if (isLoading || !form || !draftConfig) {
    return (
      <div className="computer-page">
        <PageHeader
          eyebrow="Capability Gates"
          title="Computer Use"
          description="OS-level capability gates, command allowlists, filesystem scope, and audit policy."
        />
        <Skeleton height={320} />
      </div>
    );
  }

  const update = <K extends keyof ComputerUseConfig>(key: K, value: ComputerUseConfig[K]) =>
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const enabledCapabilities = CAPABILITY_FIELDS.filter((field) => draftConfig[field.key]);
  const enabledToolCount = enabledCapabilities.reduce(
    (total, field) => total + field.tools.length,
    0
  );
  const privilegedEnabled = draftConfig.allow_shell || draftConfig.allow_filesystem;
  const auditConfigured = Boolean(draftConfig.audit_log_path?.trim());
  const filesystemConfigured = Boolean(draftConfig.filesystem_root?.trim());
  const shellConfigured = normalizedAllowlist.length > 0;

  const warnings = [
    !draftConfig.enabled
      ? 'Master switch OFF: every Computer Use tool will short-circuit with a denied response.'
      : null,
    draftConfig.allow_shell && !shellConfigured
      ? 'Shell is enabled but the allowlist is empty, so every shell request will still be denied.'
      : null,
    draftConfig.allow_filesystem && !filesystemConfigured
      ? 'Filesystem access is enabled without a filesystem root. All path resolution will be refused.'
      : null,
    privilegedEnabled && !auditConfigured
      ? 'Privileged access is active without a JSONL audit sink. Operator actions will not persist to disk.'
      : null,
  ].filter((entry): entry is string => Boolean(entry));

  const onSave = async () => {
    const next: ComputerUseConfig = draftConfig;
    if (next.enabled && (next.allow_shell || next.allow_filesystem)) {
      const ok = await confirm({
        title: 'Enable privileged Computer Use?',
        description:
          'Shell and filesystem access can mutate the host. Review allowlist, filesystem root, and audit logging before continuing.',
        confirmLabel: 'Apply policy',
        cancelLabel: 'Cancel',
        tone: 'danger',
      });
      if (!ok) return;
    }
    mutation.mutate(next);
  };

  return (
    <div className="computer-page">
      <PageHeader
        eyebrow="Capability Gates"
        title="Computer Use"
        description="Runtime safety policy for OS-level tools exposed by Baselithbot. Changes are persisted in the runtime overlay and applied on the next agent rebuild."
        actions={
          <div className="inline">
            <button
              type="button"
              className="btn ghost"
              disabled={!isDirty || mutation.isPending}
              onClick={() => data && syncFromSource(data.current)}
            >
              Reset
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={!isDirty || mutation.isPending}
              onClick={onSave}
            >
              <Icon path={paths.check} size={14} />
              {mutation.isPending ? 'Saving…' : 'Save policy'}
            </button>
          </div>
        }
      />

      <Panel className="computer-hero-panel">
        <div className="computer-hero">
          <div className="computer-hero-copy">
            <span className="badge muted">runtime overlay</span>
            <h2>Operator-level access policy</h2>
            <p>
              This surface controls which desktop, shell, and filesystem tools the plugin can
              expose. Every enabled capability expands the live tool surface for the next
              Baselithbot agent instance.
            </p>

            <div className="chip-row">
              <span className={`badge ${draftConfig.enabled ? 'ok' : 'err'}`}>
                {draftConfig.enabled ? 'armed' : 'locked'}
              </span>
              <span className={`badge ${privilegedEnabled ? 'warn' : 'muted'}`}>
                {privilegedEnabled ? 'privileged path open' : 'no privileged access'}
              </span>
              <span className={`badge ${auditConfigured ? 'ok' : 'warn'}`}>
                {auditConfigured ? 'audit persisted' : 'no audit sink'}
              </span>
              <span className={`badge ${isDirty ? 'warn' : 'muted'}`}>
                {isDirty ? 'unsaved changes' : 'saved'}
              </span>
            </div>

            <div className="computer-hero-metrics">
              <div className="computer-hero-metric">
                <span className="meta-label">Guarded tools</span>
                <strong>{formatNumber(enabledToolCount)}</strong>
                <span className="muted">currently exposed by this policy</span>
              </div>
              <div className="computer-hero-metric">
                <span className="meta-label">Shell allowlist</span>
                <strong>{formatNumber(normalizedAllowlist.length)}</strong>
                <span className="muted">{summariseAllowlist(normalizedAllowlist)}</span>
              </div>
              <div className="computer-hero-metric">
                <span className="meta-label">Filesystem boundary</span>
                <strong>{filesystemConfigured ? 'Scoped' : 'Unset'}</strong>
                <span className="muted">
                  {draftConfig.filesystem_root?.trim() || 'Configure a root to allow file ops'}
                </span>
              </div>
            </div>
          </div>

          <div className="computer-switch-card">
            <div className="computer-switch-head">
              <span className="meta-label">Master switch</span>
              <span className={`badge ${draftConfig.enabled ? 'ok' : 'err'}`}>
                {draftConfig.enabled ? 'enabled' : 'disabled'}
              </span>
            </div>

            <label className="computer-switch">
              <input
                type="checkbox"
                checked={draftConfig.enabled}
                onChange={(event) => update('enabled', event.target.checked)}
              />
              <span className="computer-switch-copy">
                <strong>
                  {draftConfig.enabled ? 'Computer Use armed' : 'Computer Use locked'}
                </strong>
                <span>
                  When disabled, every Computer Use entry point returns a denied result without
                  touching the OS.
                </span>
              </span>
            </label>

            <div className="computer-switch-meta">
              <div className="computer-kv">
                <span>Agent rebuild</span>
                <span>Required after save</span>
              </div>
              <div className="computer-kv">
                <span>Audit log</span>
                <span>{draftConfig.audit_log_path?.trim() || 'Not configured'}</span>
              </div>
              <div className="computer-kv">
                <span>Write ceiling</span>
                <span>{formatNumber(draftConfig.filesystem_max_bytes)} bytes</span>
              </div>
            </div>
          </div>
        </div>
      </Panel>

      <section className="grid grid-cols-4">
        <StatCard
          label="Capabilities On"
          value={`${enabledCapabilities.length}/${CAPABILITY_FIELDS.length}`}
          sub="feature gates raised"
          iconPath={paths.shield}
          accent="teal"
        />
        <StatCard
          label="Tool Surface"
          value={formatNumber(enabledToolCount)}
          sub="MCP tools reachable"
          iconPath={paths.sparkles}
          accent="cyan"
        />
        <StatCard
          label="Allowlist Entries"
          value={formatNumber(normalizedAllowlist.length)}
          sub={draftConfig.allow_shell ? 'shell routing policy' : 'shell capability off'}
          iconPath={paths.terminal}
          accent="amber"
        />
        <StatCard
          label="Audit State"
          value={auditConfigured ? 'Persisted' : 'Ephemeral'}
          sub={auditConfigured ? 'JSONL path configured' : 'no on-disk trail'}
          iconPath={paths.activity}
          accent={auditConfigured ? 'violet' : 'rose'}
        />
      </section>

      <section className="grid grid-split-2-1">
        <Panel
          title="Capability matrix"
          tag={`${formatNumber(enabledCapabilities.length)} active`}
          className="computer-capability-panel"
        >
          <div className="computer-capability-grid">
            {CAPABILITY_FIELDS.map((field) => {
              const enabled = draftConfig[field.key];
              return (
                <article
                  key={field.key}
                  className={[
                    'computer-capability-card',
                    enabled ? 'is-enabled' : '',
                    field.danger ? 'is-danger' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  <div className="computer-capability-head">
                    <div className="computer-capability-icon">
                      <Icon path={paths[field.icon]} size={16} />
                    </div>
                    <span className={`badge ${enabled ? capabilityTone(field.accent) : 'muted'}`}>
                      {enabled ? 'enabled' : 'disabled'}
                    </span>
                  </div>

                  <div className="computer-capability-body">
                    <div>
                      <div className="computer-capability-title-row">
                        <strong>{field.label}</strong>
                        {field.danger && <span className="badge warn">privileged</span>}
                      </div>
                      <p>{field.description}</p>
                    </div>

                    <label className="computer-toggle-row">
                      <span className="meta-label">Allow capability</span>
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={(event) => update(field.key, event.target.checked)}
                      />
                    </label>

                    <div className="computer-tool-list">
                      {field.tools.map((tool) => (
                        <span key={tool} className="computer-tool-chip mono">
                          {tool}
                        </span>
                      ))}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </Panel>

        <Panel title="Risk review" tag="before save">
          <div className="stack-section">
            <div className="detail-grid">
              <div className="meta-tile">
                <span className="meta-label">Shell</span>
                <span>{draftConfig.allow_shell ? 'Enabled' : 'Disabled'}</span>
              </div>
              <div className="meta-tile">
                <span className="meta-label">Filesystem</span>
                <span>{draftConfig.allow_filesystem ? 'Enabled' : 'Disabled'}</span>
              </div>
              <div className="meta-tile">
                <span className="meta-label">Timeout</span>
                <span>{draftConfig.shell_timeout_seconds}s</span>
              </div>
              <div className="meta-tile">
                <span className="meta-label">Audit trail</span>
                <span>{auditConfigured ? 'Persisted' : 'Not persisted'}</span>
              </div>
            </div>

            {warnings.length === 0 ? (
              <div className="info-block">
                Current policy is internally coherent: enabled capabilities have the supporting
                guardrails they need.
              </div>
            ) : (
              <div className="computer-warning-list">
                {warnings.map((warning) => (
                  <div key={warning} className="computer-warning-item">
                    <Icon path={paths.shieldOff} size={14} />
                    <span>{warning}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="computer-policy-note">
              Privileged capabilities are intentionally split from observation capabilities. Keep
              screenshots, mouse, and keyboard separate from shell/filesystem unless the task
              genuinely needs host mutation.
            </div>
          </div>
        </Panel>
      </section>

      <Panel title="Execution guardrails" tag="shell + filesystem">
        <div className="computer-guard-grid">
          <div className="computer-allowlist-column">
            <div className="computer-field-head">
              <div>
                <div className="section-label">Shell allowlist</div>
                <div className="computer-field-copy">
                  One entry per line. First-token exact match or prefix match. Empty means shell
                  execution stays blocked even when the shell capability is on.
                </div>
              </div>
              <span className={`badge ${shellConfigured ? 'ok' : 'warn'}`}>
                {formatNumber(normalizedAllowlist.length)} entries
              </span>
            </div>

            <textarea
              className="textarea computer-allowlist"
              value={allowlistDraft}
              onChange={(event) => setAllowlistDraft(event.target.value)}
              placeholder={'ls\npwd\ngit status\npython -m pytest'}
            />
          </div>

          <div className="computer-settings-column">
            <label className="form-row">
              <span>Shell timeout (seconds)</span>
              <input
                type="number"
                min={1}
                max={600}
                step={1}
                className="input"
                value={draftConfig.shell_timeout_seconds}
                onChange={(event) => update('shell_timeout_seconds', Number(event.target.value))}
              />
            </label>

            <label className="form-row">
              <span>Filesystem root</span>
              <input
                type="text"
                className="input mono"
                placeholder="/tmp/baselithbot-sandbox"
                value={draftConfig.filesystem_root ?? ''}
                onChange={(event) => update('filesystem_root', event.target.value || null)}
              />
            </label>

            <label className="form-row">
              <span>Filesystem max bytes / write</span>
              <input
                type="number"
                min={1}
                step={1}
                className="input"
                value={draftConfig.filesystem_max_bytes}
                onChange={(event) => update('filesystem_max_bytes', Number(event.target.value))}
              />
            </label>

            <label className="form-row">
              <span>Audit log path (JSONL)</span>
              <input
                type="text"
                className="input mono"
                placeholder="/tmp/baselithbot-sandbox/audit.jsonl"
                value={draftConfig.audit_log_path ?? ''}
                onChange={(event) => update('audit_log_path', event.target.value || null)}
              />
            </label>
          </div>
        </div>
      </Panel>

      <Panel title="Integrated tool surface" tag={`${formatNumber(enabledToolCount)} exposed`}>
        {enabledCapabilities.length === 0 ? (
          <div className="info-block">
            No Computer Use capability is currently enabled, so Baselithbot will not expose any
            desktop, shell, or filesystem MCP entry point.
          </div>
        ) : (
          <div className="computer-integrated-grid">
            {enabledCapabilities.map((field) => (
              <section key={field.key} className="computer-integrated-card">
                <div className="computer-integrated-head">
                  <div>
                    <div className="section-label">{field.label}</div>
                    <strong>{formatNumber(field.tools.length)} tool(s)</strong>
                  </div>
                  <span className={`badge ${capabilityTone(field.accent)}`}>live</span>
                </div>
                <div className="computer-tool-list">
                  {field.tools.map((tool) => (
                    <span key={tool} className="computer-tool-chip mono">
                      {tool}
                    </span>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
