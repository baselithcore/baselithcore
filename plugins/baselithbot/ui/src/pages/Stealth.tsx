import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type StealthConfig } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';

export function Stealth() {
  const qc = useQueryClient();
  const { push } = useToasts();

  const { data, isLoading } = useQuery({
    queryKey: ['stealth'],
    queryFn: api.stealth,
    refetchInterval: 15_000,
  });

  const [form, setForm] = useState<StealthConfig | null>(null);
  const [langDraft, setLangDraft] = useState('');
  const [uaDraft, setUaDraft] = useState('');

  useEffect(() => {
    if (data && !form) {
      setForm(data.current);
      setLangDraft(data.current.spoof_languages.join(','));
      setUaDraft(data.current.user_agents.join('\n'));
    }
  }, [data, form]);

  const mutation = useMutation({
    mutationFn: (cfg: StealthConfig) => api.updateStealth(cfg),
    onSuccess: (res) => {
      setForm(res.current);
      setLangDraft(res.current.spoof_languages.join(','));
      setUaDraft(res.current.user_agents.join('\n'));
      qc.invalidateQueries({ queryKey: ['stealth'] });
      push({
        tone: 'success',
        title: 'Stealth saved',
        description: `${res.current.enabled ? 'enabled' : 'disabled'} · agent rebuilds on next run`,
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
        <PageHeader title="Stealth" description="Playwright BrowserContext stealth gates." />
        <Skeleton height={192} />
      </div>
    );
  }

  const update = <K extends keyof StealthConfig>(
    key: K,
    value: StealthConfig[K],
  ) => setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const onSave = () => {
    const langs = langDraft
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const uas = uaDraft
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    mutation.mutate({ ...form, spoof_languages: langs, user_agents: uas });
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Stealth"
        description="Playwright BrowserContext fingerprint and rotation."
      />

      <Panel>
        <header className="px-4 pt-4">
          <h3 className="text-sm font-semibold">Toggles</h3>
        </header>
        <div className="grid grid-cols-1 gap-3 px-4 pb-4 pt-3 sm:grid-cols-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => update('enabled', e.target.checked)}
            />
            Stealth master
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.rotate_user_agent}
              onChange={(e) => update('rotate_user_agent', e.target.checked)}
            />
            Rotate User-Agent
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.mask_webdriver}
              onChange={(e) => update('mask_webdriver', e.target.checked)}
            />
            Mask <code>navigator.webdriver</code>
          </label>
        </div>
      </Panel>

      <Panel>
        <header className="px-4 pt-4">
          <h3 className="text-sm font-semibold">Spoof</h3>
        </header>
        <div className="grid grid-cols-1 gap-3 px-4 pb-4 pt-3 sm:grid-cols-2">
          <label className="text-xs">
            <span className="block text-zinc-400">
              Languages (comma-separated)
            </span>
            <input
              type="text"
              className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 p-2 font-mono"
              value={langDraft}
              onChange={(e) => setLangDraft(e.target.value)}
              placeholder="en-US,en"
            />
          </label>
          <label className="text-xs">
            <span className="block text-zinc-400">Timezone</span>
            <input
              type="text"
              className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 p-2 font-mono"
              value={form.spoof_timezone}
              onChange={(e) => update('spoof_timezone', e.target.value)}
              placeholder="UTC"
            />
          </label>
        </div>
      </Panel>

      <Panel>
        <header className="px-4 pt-4">
          <h3 className="text-sm font-semibold">User-Agent pool</h3>
          <p className="text-xs text-zinc-400">
            One UA string per line. Empty pool falls back to the built-in defaults.
          </p>
        </header>
        <div className="px-4 pb-4 pt-3">
          <textarea
            className="h-44 w-full rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-xs"
            value={uaDraft}
            onChange={(e) => setUaDraft(e.target.value)}
          />
        </div>
      </Panel>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            if (data) {
              setForm(data.current);
              setLangDraft(data.current.spoof_languages.join(','));
              setUaDraft(data.current.user_agents.join('\n'));
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
