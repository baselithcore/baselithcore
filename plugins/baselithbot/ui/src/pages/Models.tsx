import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type FailoverEntry,
  type LLMProvider,
  type ModelPreferences,
  type VisionProvider,
} from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { ProviderKeyEditor } from '../components/ProviderKeyEditor';

export function Models() {
  const qc = useQueryClient();
  const { push } = useToasts();

  const { data, isLoading } = useQuery({
    queryKey: ['models'],
    queryFn: api.models,
    refetchInterval: 15_000,
  });

  const [form, setForm] = useState<ModelPreferences | null>(null);

  useEffect(() => {
    if (data && !form) setForm(data.current);
  }, [data, form]);

  const llmProviders = useMemo(() => {
    if (!data) return [] as LLMProvider[];
    return Object.keys(data.options.llm_providers) as LLMProvider[];
  }, [data]);

  const visionProviders = useMemo(() => {
    if (!data) return [] as VisionProvider[];
    return Object.keys(data.options.vision_providers) as VisionProvider[];
  }, [data]);

  const mutation = useMutation({
    mutationFn: (prefs: ModelPreferences) => api.updateModels(prefs),
    onSuccess: (res) => {
      setForm(res.current);
      qc.invalidateQueries({ queryKey: ['models'] });
      push({
        tone: 'success',
        title: 'Model preferences saved',
        description: `${res.current.provider} · ${res.current.model}`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Save failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  if (isLoading || !data || !form) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Skeleton height={80} />
        <Skeleton height={240} />
      </div>
    );
  }

  const llmModels = data.options.llm_providers[form.provider] ?? [];
  const visionModels = data.options.vision_providers[form.vision_provider] ?? [];

  const update = <K extends keyof ModelPreferences>(key: K, value: ModelPreferences[K]) => {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const addFailover = () => {
    const next: FailoverEntry = {
      provider: form.provider,
      model: form.model,
      cooldown_seconds: 30,
    };
    update('failover_chain', [...form.failover_chain, next]);
  };

  const updateFailover = (index: number, patch: Partial<FailoverEntry>) => {
    const next = form.failover_chain.map((entry, i) =>
      i === index ? { ...entry, ...patch } : entry
    );
    update('failover_chain', next);
  };

  const removeFailover = (index: number) => {
    update(
      'failover_chain',
      form.failover_chain.filter((_, i) => i !== index)
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Models"
        title="LLM & Vision providers"
        description="Choose the reasoning model, vision model, and optional failover chain. Changes apply on the next agent run."
        actions={
          <div className="inline">
            <button
              type="button"
              className="btn ghost sm"
              disabled={mutation.isPending}
              onClick={() => setForm(data.current)}
            >
              <Icon path={paths.refresh} size={12} />
              Revert
            </button>
            <button
              type="button"
              className="btn primary sm"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(form)}
            >
              <Icon path={paths.check} size={12} />
              {mutation.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
        }
      />

      <section className="grid grid-cols-2">
        <Panel title="Reasoning / planning" tag="LLM">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="form-row">
              <label htmlFor="llm-provider">Provider</label>
              <select
                id="llm-provider"
                className="select"
                value={form.provider}
                onChange={(e) => {
                  const provider = e.target.value as LLMProvider;
                  const first = data.options.llm_providers[provider]?.[0] ?? form.model;
                  setForm((prev) => (prev ? { ...prev, provider, model: first } : prev));
                }}
              >
                {llmProviders.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-row">
              <label htmlFor="llm-model">Model</label>
              <input
                id="llm-model"
                className="input"
                list="llm-catalog"
                value={form.model}
                onChange={(e) => update('model', e.target.value)}
              />
              <datalist id="llm-catalog">
                {llmModels.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </div>

            <div className="inline">
              <div className="form-row" style={{ flex: 1 }}>
                <label htmlFor="llm-temp">Temperature</label>
                <input
                  id="llm-temp"
                  className="input"
                  type="number"
                  min={0}
                  max={2}
                  step={0.05}
                  value={form.temperature}
                  onChange={(e) => update('temperature', Number(e.target.value))}
                />
              </div>
              <div className="form-row" style={{ flex: 1 }}>
                <label htmlFor="llm-maxtok">Max tokens</label>
                <input
                  id="llm-maxtok"
                  className="input"
                  type="number"
                  min={1}
                  max={200_000}
                  placeholder="(provider default)"
                  value={form.max_tokens ?? ''}
                  onChange={(e) => {
                    const v = e.target.value.trim();
                    update('max_tokens', v ? Number(v) : null);
                  }}
                />
              </div>
            </div>
          </div>
        </Panel>

        <Panel title="Vision / screenshot" tag="vision">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="form-row">
              <label htmlFor="vis-provider">Provider</label>
              <select
                id="vis-provider"
                className="select"
                value={form.vision_provider}
                onChange={(e) => {
                  const provider = e.target.value as VisionProvider;
                  const first = data.options.vision_providers[provider]?.[0] ?? form.vision_model;
                  setForm((prev) =>
                    prev
                      ? {
                          ...prev,
                          vision_provider: provider,
                          vision_model: first,
                        }
                      : prev
                  );
                }}
              >
                {visionProviders.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-row">
              <label htmlFor="vis-model">Model</label>
              <input
                id="vis-model"
                className="input"
                list="vision-catalog"
                value={form.vision_model}
                onChange={(e) => update('vision_model', e.target.value)}
              />
              <datalist id="vision-catalog">
                {visionModels.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </div>
          </div>
        </Panel>
      </section>

      <Panel title="Provider API keys" tag="secret">
        <ProviderKeyEditor
          providers={Array.from(new Set([...llmProviders, ...visionProviders]))}
          description="Keys set here override the env vars at runtime. Only the last 4 characters are ever echoed back by the API — the stored value is Fernet-encrypted on disk."
        />
      </Panel>

      <Panel title="Failover chain" tag={`${form.failover_chain.length} entries`}>
        <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
          Ordered list. The router tries each provider in turn, skipping any that failed within the
          cooldown window.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {form.failover_chain.map((entry, i) => (
            <div
              key={i}
              className="inline"
              style={{
                alignItems: 'flex-end',
                padding: 10,
                border: '1px solid var(--panel-border)',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(15,19,25,0.4)',
              }}
            >
              <div className="form-row" style={{ flex: 1 }}>
                <label>Provider</label>
                <select
                  className="select"
                  value={entry.provider}
                  onChange={(e) =>
                    updateFailover(i, {
                      provider: e.target.value as LLMProvider,
                    })
                  }
                >
                  {llmProviders.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-row" style={{ flex: 2 }}>
                <label>Model</label>
                <input
                  className="input"
                  value={entry.model}
                  onChange={(e) => updateFailover(i, { model: e.target.value })}
                />
              </div>
              <div className="form-row" style={{ flex: 1 }}>
                <label>Cooldown (s)</label>
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={3600}
                  value={entry.cooldown_seconds}
                  onChange={(e) =>
                    updateFailover(i, {
                      cooldown_seconds: Number(e.target.value) || 0,
                    })
                  }
                />
              </div>
              <button type="button" className="btn danger xs" onClick={() => removeFailover(i)}>
                <Icon path={paths.trash} size={12} />
                Remove
              </button>
            </div>
          ))}
        </div>
        <button type="button" className="btn sm" style={{ marginTop: 12 }} onClick={addFailover}>
          <Icon path={paths.plus} size={12} />
          Add failover entry
        </button>
      </Panel>
    </div>
  );
}
