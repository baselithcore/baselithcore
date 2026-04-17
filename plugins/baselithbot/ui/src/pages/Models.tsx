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
import { EmptyState } from '../components/EmptyState';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { ProviderKeyEditor } from '../components/ProviderKeyEditor';

const PROVIDER_LABELS: Record<LLMProvider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  ollama: 'Ollama (local)',
  huggingface: 'HuggingFace',
};

const VISION_LABELS: Record<VisionProvider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google Gemini',
  ollama: 'Ollama (local)',
};

function prefsEqual(a: ModelPreferences, b: ModelPreferences): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

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
        <Skeleton height={180} />
      </div>
    );
  }

  const llmModels = data.options.llm_providers[form.provider] ?? [];
  const visionModels = data.options.vision_providers[form.vision_provider] ?? [];
  const dirty = !prefsEqual(form, data.current);

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

  const moveFailover = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= form.failover_chain.length) return;
    const next = [...form.failover_chain];
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    update('failover_chain', next);
  };

  const resetAll = () => setForm(data.current);

  return (
    <div className="models-page" style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Models"
        title="LLM & Vision configuration"
        description="Pick the reasoning and screenshot-analysis models, manage provider keys, and declare a failover chain. Changes apply on the next agent run."
        actions={
          <div className="inline">
            {dirty && (
              <span className="pill warn" title="Unsaved changes">
                <span className="dot" />
                Unsaved
              </span>
            )}
            <button
              type="button"
              className="btn ghost sm"
              disabled={mutation.isPending || !dirty}
              onClick={resetAll}
            >
              <Icon path={paths.refresh} size={12} />
              Revert
            </button>
            <button
              type="button"
              className="btn primary sm"
              disabled={mutation.isPending || !dirty}
              onClick={() => mutation.mutate(form)}
            >
              <Icon path={paths.check} size={12} />
              {mutation.isPending ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        }
      />

      <section className="models-summary">
        <div className="models-summary-card">
          <div className="models-summary-label">
            <Icon path={paths.sparkles} size={12} />
            Reasoning
          </div>
          <div className="models-summary-value">{data.current.model}</div>
          <div className="models-summary-sub">
            <span className="badge ok">{PROVIDER_LABELS[data.current.provider]}</span>
            <span className="muted">T {data.current.temperature.toFixed(2)}</span>
            <span className="muted">max {data.current.max_tokens ?? '—'}</span>
          </div>
        </div>
        <div className="models-summary-card">
          <div className="models-summary-label">
            <Icon path={paths.radar} size={12} />
            Vision
          </div>
          <div className="models-summary-value">{data.current.vision_model}</div>
          <div className="models-summary-sub">
            <span className="badge ok">{VISION_LABELS[data.current.vision_provider]}</span>
          </div>
        </div>
        <div className="models-summary-card">
          <div className="models-summary-label">
            <Icon path={paths.cable} size={12} />
            Failover
          </div>
          <div className="models-summary-value">
            {data.current.failover_chain.length}{' '}
            <span className="models-summary-unit">
              {data.current.failover_chain.length === 1 ? 'entry' : 'entries'}
            </span>
          </div>
          <div className="models-summary-sub">
            {data.current.failover_chain.length === 0 ? (
              <span className="badge muted">No fallback</span>
            ) : (
              <span className="badge">
                {data.current.failover_chain
                  .map((entry) => PROVIDER_LABELS[entry.provider])
                  .slice(0, 3)
                  .join(' → ')}
                {data.current.failover_chain.length > 3 ? ' …' : ''}
              </span>
            )}
          </div>
        </div>
      </section>

      <Panel title="Reasoning model" tag="LLM">
        <p className="muted" style={{ fontSize: 12, marginTop: 0, marginBottom: 14 }}>
          Chosen provider serves as the primary for planning and tool-selection calls.
        </p>

        <div className="provider-grid">
          {llmProviders.map((p) => {
            const active = form.provider === p;
            const count = data.options.llm_providers[p]?.length ?? 0;
            return (
              <button
                key={p}
                type="button"
                className={`provider-tile ${active ? 'active' : ''}`}
                onClick={() => {
                  const first = data.options.llm_providers[p]?.[0] ?? form.model;
                  setForm((prev) => (prev ? { ...prev, provider: p, model: first } : prev));
                }}
              >
                <div className="provider-tile-head">
                  <span className="provider-tile-name">{PROVIDER_LABELS[p]}</span>
                  {active && (
                    <span className="badge ok">
                      <Icon path={paths.check} size={10} /> Active
                    </span>
                  )}
                </div>
                <div className="provider-tile-meta">
                  {count} model{count === 1 ? '' : 's'} available
                </div>
              </button>
            );
          })}
        </div>

        <div className="models-grid" style={{ marginTop: 16 }}>
          <div className="form-row">
            <label htmlFor="llm-model">Model ID</label>
            <input
              id="llm-model"
              className="input"
              list="llm-catalog"
              value={form.model}
              placeholder="e.g. gpt-4o, claude-opus-4-7"
              onChange={(e) => update('model', e.target.value)}
            />
            <datalist id="llm-catalog">
              {llmModels.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            <span className="form-hint">
              Pick from the catalog or type any ID the provider accepts.
            </span>
          </div>

          <div className="form-row">
            <label htmlFor="llm-temp">
              Temperature <span className="form-hint-inline">0 = deterministic, 2 = wild</span>
            </label>
            <div className="slider-row">
              <input
                id="llm-temp"
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={form.temperature}
                onChange={(e) => update('temperature', Number(e.target.value))}
                className="slider"
              />
              <input
                type="number"
                className="input slider-number"
                min={0}
                max={2}
                step={0.05}
                value={form.temperature}
                onChange={(e) => update('temperature', Number(e.target.value))}
              />
            </div>
          </div>

          <div className="form-row">
            <label htmlFor="llm-maxtok">
              Max tokens <span className="form-hint-inline">leave empty for provider default</span>
            </label>
            <input
              id="llm-maxtok"
              className="input"
              type="number"
              min={1}
              max={200_000}
              placeholder="provider default"
              value={form.max_tokens ?? ''}
              onChange={(e) => {
                const v = e.target.value.trim();
                update('max_tokens', v ? Number(v) : null);
              }}
            />
          </div>
        </div>
      </Panel>

      <Panel title="Vision model" tag="screenshot analysis">
        <p className="muted" style={{ fontSize: 12, marginTop: 0, marginBottom: 14 }}>
          Used on every browser step to interpret screenshots and decide the next action.
        </p>

        <div className="provider-grid">
          {visionProviders.map((p) => {
            const active = form.vision_provider === p;
            const count = data.options.vision_providers[p]?.length ?? 0;
            return (
              <button
                key={p}
                type="button"
                className={`provider-tile ${active ? 'active' : ''}`}
                onClick={() => {
                  const first = data.options.vision_providers[p]?.[0] ?? form.vision_model;
                  setForm((prev) =>
                    prev ? { ...prev, vision_provider: p, vision_model: first } : prev
                  );
                }}
              >
                <div className="provider-tile-head">
                  <span className="provider-tile-name">{VISION_LABELS[p]}</span>
                  {active && (
                    <span className="badge ok">
                      <Icon path={paths.check} size={10} /> Active
                    </span>
                  )}
                </div>
                <div className="provider-tile-meta">
                  {count} model{count === 1 ? '' : 's'} available
                </div>
              </button>
            );
          })}
        </div>

        <div className="form-row" style={{ marginTop: 16 }}>
          <label htmlFor="vis-model">Vision model ID</label>
          <input
            id="vis-model"
            className="input"
            list="vision-catalog"
            value={form.vision_model}
            placeholder="e.g. gpt-4o, llava:13b"
            onChange={(e) => update('vision_model', e.target.value)}
          />
          <datalist id="vision-catalog">
            {visionModels.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>
      </Panel>

      <Panel title="Provider API keys" tag="encrypted at rest">
        <ProviderKeyEditor
          providers={Array.from(new Set([...llmProviders, ...visionProviders]))}
          description="Keys set here override env vars at runtime. Only the last 4 characters are echoed back by the API — stored values are Fernet-encrypted on disk."
        />
      </Panel>

      <Panel
        title="Failover chain"
        tag={
          form.failover_chain.length === 0
            ? 'disabled'
            : `${form.failover_chain.length} ${form.failover_chain.length === 1 ? 'entry' : 'entries'}`
        }
      >
        <p className="muted" style={{ fontSize: 12, marginTop: 0, marginBottom: 14 }}>
          Ordered fallback list. The router tries each provider in turn when the primary call
          errors; failed entries enter a cooldown window before being retried.
        </p>

        {form.failover_chain.length === 0 ? (
          <EmptyState
            title="No failover entries"
            description="Add a backup provider so transient outages on the primary don't abort runs."
            action={
              <button type="button" className="btn sm" onClick={addFailover}>
                <Icon path={paths.plus} size={12} />
                Add first entry
              </button>
            }
          />
        ) : (
          <>
            <ol className="failover-list">
              {form.failover_chain.map((entry, i) => (
                <li key={i} className="failover-row">
                  <div className="failover-rank">
                    <span className="failover-rank-number">{i + 1}</span>
                    <div className="failover-rank-ctrl">
                      <button
                        type="button"
                        className="btn ghost xs"
                        disabled={i === 0}
                        onClick={() => moveFailover(i, -1)}
                        aria-label="Move up"
                        title="Move up"
                      >
                        ▲
                      </button>
                      <button
                        type="button"
                        className="btn ghost xs"
                        disabled={i === form.failover_chain.length - 1}
                        onClick={() => moveFailover(i, 1)}
                        aria-label="Move down"
                        title="Move down"
                      >
                        ▼
                      </button>
                    </div>
                  </div>

                  <div className="failover-fields">
                    <div className="form-row">
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
                            {PROVIDER_LABELS[p]}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="form-row">
                      <label>Model</label>
                      <input
                        className="input"
                        list={`failover-catalog-${i}`}
                        value={entry.model}
                        onChange={(e) => updateFailover(i, { model: e.target.value })}
                      />
                      <datalist id={`failover-catalog-${i}`}>
                        {(data.options.llm_providers[entry.provider] ?? []).map((m) => (
                          <option key={m} value={m} />
                        ))}
                      </datalist>
                    </div>
                    <div className="form-row">
                      <label>
                        Cooldown <span className="form-hint-inline">seconds</span>
                      </label>
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
                  </div>

                  <button
                    type="button"
                    className="btn danger xs failover-remove"
                    onClick={() => removeFailover(i)}
                    title="Remove entry"
                  >
                    <Icon path={paths.trash} size={12} />
                  </button>
                </li>
              ))}
            </ol>
            <div style={{ marginTop: 12 }}>
              <button type="button" className="btn sm" onClick={addFailover}>
                <Icon path={paths.plus} size={12} />
                Add failover entry
              </button>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
