import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ComputerUseConfig } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { useConfirm } from '../components/ConfirmProvider';

const CAPABILITY_FIELDS: Array<{
  key: keyof ComputerUseConfig;
  label: string;
  description: string;
  danger?: boolean;
}> = [
  {
    key: 'allow_screenshot',
    label: 'Screenshot',
    description: 'mss-based screen capture (full / region).',
  },
  {
    key: 'allow_mouse',
    label: 'Mouse',
    description: 'pyautogui mouse_move / mouse_click / mouse_scroll.',
  },
  {
    key: 'allow_keyboard',
    label: 'Keyboard',
    description: 'pyautogui kbd_type / kbd_press / kbd_hotkey.',
  },
  {
    key: 'allow_shell',
    label: 'Shell',
    description: 'Subprocess invocations gated by allowlist.',
    danger: true,
  },
  {
    key: 'allow_filesystem',
    label: 'Filesystem',
    description: 'Read / write / list within filesystem_root.',
    danger: true,
  },
];

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

  useEffect(() => {
    if (data && !form) {
      setForm(data.current);
      setAllowlistDraft(data.current.allowed_shell_commands.join('\n'));
    }
  }, [data, form]);

  const mutation = useMutation({
    mutationFn: (cfg: ComputerUseConfig) => api.updateComputerUse(cfg),
    onSuccess: (res) => {
      setForm(res.current);
      setAllowlistDraft(res.current.allowed_shell_commands.join('\n'));
      qc.invalidateQueries({ queryKey: ['computer-use'] });
      push({
        tone: 'success',
        title: 'Computer Use saved',
        description: `master ${res.current.enabled ? 'on' : 'off'} · agent will rebuild on next run`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Save failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  if (isLoading || !form) {
    return (
      <div className="space-y-4">
        <PageHeader title="Computer Use" description="OS-level capability gates and audit." />
        <Skeleton height={192} />
      </div>
    );
  }

  const update = <K extends keyof ComputerUseConfig>(
    key: K,
    value: ComputerUseConfig[K],
  ) => setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const onSave = async () => {
    const allowlist = allowlistDraft
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    const next: ComputerUseConfig = { ...form, allowed_shell_commands: allowlist };

    if (next.enabled && (next.allow_shell || next.allow_filesystem)) {
      const ok = await confirm({
        title: 'Enable privileged capabilities?',
        description:
          'Shell and/or filesystem access are dangerous. Audit log path SHOULD be set. Continue?',
        confirmLabel: 'Enable',
        tone: 'danger',
      });
      if (!ok) return;
    }
    mutation.mutate(next);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Computer Use"
        description="OS-level capability gates with allowlist + audit log."
      />

      <Panel>
        <header className="px-4 pt-4">
          <h3 className="text-sm font-semibold">Master switch</h3>
          <p className="text-xs text-zinc-400">
            When OFF every Computer Use tool returns <code>denied</code> without
            touching the OS.
          </p>
        </header>
        <div className="px-4 pb-4 pt-3">
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => update('enabled', e.target.checked)}
            />
            <span className="text-sm">
              <strong>{form.enabled ? 'ENABLED' : 'DISABLED'}</strong>
            </span>
          </label>
        </div>
      </Panel>

      <Panel>
        <header className="px-4 pt-4">
          <h3 className="text-sm font-semibold">Capabilities</h3>
        </header>
        <div className="grid grid-cols-1 gap-3 px-4 pb-4 pt-3 sm:grid-cols-2">
          {CAPABILITY_FIELDS.map((field) => (
            <label
              key={field.key as string}
              className={`flex items-start gap-3 rounded border p-3 ${
                field.danger
                  ? 'border-amber-700/50 bg-amber-950/10'
                  : 'border-zinc-800'
              }`}
            >
              <input
                type="checkbox"
                checked={Boolean(form[field.key])}
                onChange={(e) => update(field.key, e.target.checked as never)}
                className="mt-1"
              />
              <span className="text-sm">
                <span className="font-medium">{field.label}</span>
                {field.danger ? (
                  <span className="ml-2 rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] uppercase text-amber-300">
                    privileged
                  </span>
                ) : null}
                <span className="block text-xs text-zinc-400">
                  {field.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      </Panel>

      <Panel>
        <header className="px-4 pt-4">
          <h3 className="text-sm font-semibold">Shell allowlist</h3>
          <p className="text-xs text-zinc-400">
            One first-token entry per line. Exact-match or space-prefix
            (<code>git status</code> allows <code>git status --short</code>).
            Empty list disables shell entirely.
          </p>
        </header>
        <div className="space-y-3 px-4 pb-4 pt-3">
          <textarea
            className="h-40 w-full rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-xs"
            value={allowlistDraft}
            onChange={(e) => setAllowlistDraft(e.target.value)}
            placeholder={'ls\npwd\ngit status\necho'}
          />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="text-xs">
              <span className="block text-zinc-400">Shell timeout (s)</span>
              <input
                type="number"
                min={1}
                max={600}
                step={1}
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 p-2"
                value={form.shell_timeout_seconds}
                onChange={(e) =>
                  update('shell_timeout_seconds', Number(e.target.value))
                }
              />
            </label>
            <label className="text-xs">
              <span className="block text-zinc-400">Filesystem root</span>
              <input
                type="text"
                placeholder="/tmp/baselithbot-sandbox"
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 p-2 font-mono"
                value={form.filesystem_root ?? ''}
                onChange={(e) =>
                  update('filesystem_root', e.target.value || null)
                }
              />
            </label>
            <label className="text-xs">
              <span className="block text-zinc-400">FS max bytes / write</span>
              <input
                type="number"
                min={1}
                step={1}
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 p-2"
                value={form.filesystem_max_bytes}
                onChange={(e) =>
                  update('filesystem_max_bytes', Number(e.target.value))
                }
              />
            </label>
          </div>
          <label className="block text-xs">
            <span className="block text-zinc-400">Audit log path (JSONL)</span>
            <input
              type="text"
              placeholder="/tmp/baselithbot-sandbox/audit.jsonl"
              className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 p-2 font-mono"
              value={form.audit_log_path ?? ''}
              onChange={(e) => update('audit_log_path', e.target.value || null)}
            />
          </label>
        </div>
      </Panel>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            if (data) {
              setForm(data.current);
              setAllowlistDraft(data.current.allowed_shell_commands.join('\n'));
            }
          }}
          className="rounded border border-zinc-700 px-3 py-2 text-sm"
        >
          Reset
        </button>
        <button
          type="button"
          disabled={mutation.isPending}
          onClick={onSave}
          className="rounded bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {mutation.isPending ? 'Saving…' : 'Save & rebuild agent'}
        </button>
      </div>
    </div>
  );
}
