import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { StatCard } from '../components/StatCard';
import { useToasts } from '../components/ToastProvider';
import { api, type StealthConfig } from '../lib/api';
import { formatNumber } from '../lib/format';
import { Icon, paths, type IconName } from '../lib/icons';

type StealthToggleKey = 'enabled' | 'rotate_user_agent' | 'mask_webdriver';

type StealthToggleSpec = {
  key: StealthToggleKey;
  label: string;
  description: string;
  icon: IconName;
  accent: 'teal' | 'amber' | 'violet';
  outcomes: string[];
};

const TOGGLE_FIELDS: StealthToggleSpec[] = [
  {
    key: 'enabled',
    label: 'Stealth master',
    description: 'Turns all stealth-specific BrowserContext mutations on or off.',
    icon: 'shield',
    accent: 'teal',
    outcomes: ['Context options applied', 'Init scripts injected', 'Header spoofing enabled'],
  },
  {
    key: 'rotate_user_agent',
    label: 'Rotate User-Agent',
    description: 'Chooses a user agent from the configured pool for each new agent session.',
    icon: 'refresh',
    accent: 'amber',
    outcomes: ['Playwright user_agent set', 'Identity entropy between sessions'],
  },
  {
    key: 'mask_webdriver',
    label: 'Mask webdriver',
    description: 'Overrides navigator.webdriver and related fingerprint surfaces in JS.',
    icon: 'sparkles',
    accent: 'violet',
    outcomes: ['navigator.webdriver hidden', 'Plugin/WebGL script mutations active'],
  },
];

function normalizeLanguagesDraft(draft: string): string[] {
  return draft
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function normalizeUserAgentsDraft(draft: string): string[] {
  return draft
    .split(/\r?\n/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function sameConfig(left: StealthConfig, right: StealthConfig): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function activeUserAgent(config: StealthConfig): string {
  if (!config.enabled) return 'Browser default fingerprint path';
  if (config.user_agents.length === 0) return 'No custom User-Agent override';
  if (config.rotate_user_agent) return 'Random from pool on each agent rebuild';
  return config.user_agents[0];
}

function browserContextPreview(config: StealthConfig): Array<{ label: string; value: string }> {
  if (!config.enabled) return [];
  const preview: Array<{ label: string; value: string }> = [];
  if (config.user_agents.length > 0) {
    preview.push({
      label: 'user_agent',
      value: config.rotate_user_agent ? 'randomized from pool' : config.user_agents[0],
    });
  }
  if (config.spoof_languages.length > 0) {
    preview.push({ label: 'locale', value: config.spoof_languages[0] });
  }
  if (config.spoof_timezone.trim()) {
    preview.push({ label: 'timezone_id', value: config.spoof_timezone.trim() });
  }
  return preview;
}

function headerPreview(config: StealthConfig): Array<{ label: string; value: string }> {
  if (!config.enabled || config.spoof_languages.length === 0) return [];
  return [{ label: 'Accept-Language', value: config.spoof_languages.join(', ') }];
}

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

  const normalizedLanguages = useMemo(() => normalizeLanguagesDraft(langDraft), [langDraft]);
  const normalizedUserAgents = useMemo(() => normalizeUserAgentsDraft(uaDraft), [uaDraft]);

  const draftConfig = useMemo(
    () =>
      form
        ? {
            ...form,
            spoof_languages: normalizedLanguages,
            user_agents: normalizedUserAgents,
          }
        : null,
    [form, normalizedLanguages, normalizedUserAgents]
  );

  const isDirty = useMemo(
    () => Boolean(data && draftConfig && !sameConfig(draftConfig, data.current)),
    [data, draftConfig]
  );

  const syncFromSource = (next: StealthConfig) => {
    setForm(next);
    setLangDraft(next.spoof_languages.join(', '));
    setUaDraft(next.user_agents.join('\n'));
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
    mutationFn: (config: StealthConfig) => api.updateStealth(config),
    onSuccess: (res) => {
      syncFromSource(res.current);
      qc.invalidateQueries({ queryKey: ['stealth'] });
      qc.invalidateQueries({ queryKey: ['overview'] });
      push({
        tone: 'success',
        title: 'Stealth policy saved',
        description: 'BrowserContext spoofing rules updated for the next agent rebuild.',
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
      <div className="stealth-page">
        <PageHeader
          eyebrow="Browser Fingerprint"
          title="Stealth"
          description="BrowserContext stealth policy, identity rotation, and spoofed locale/timezone controls."
        />
        <Skeleton height={320} />
      </div>
    );
  }

  const update = <K extends keyof StealthConfig>(key: K, value: StealthConfig[K]) =>
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const enabledToggles = TOGGLE_FIELDS.filter((field) => draftConfig[field.key]);
  const contextPreview = browserContextPreview(draftConfig);
  const headers = headerPreview(draftConfig);
  const warnings = [
    !draftConfig.enabled
      ? 'Stealth is disabled, so Baselithbot will launch with the browser defaults and no spoofed context policy.'
      : null,
    draftConfig.rotate_user_agent && normalizedUserAgents.length < 2
      ? 'User-Agent rotation is enabled but the pool has fewer than two entries, so rotation adds no real entropy.'
      : null,
    !draftConfig.rotate_user_agent && normalizedUserAgents.length > 1
      ? 'Rotation is disabled: Baselithbot will always use the first User-Agent in the pool.'
      : null,
    normalizedLanguages.length === 0
      ? 'No spoofed languages configured, so Accept-Language and navigator.language overrides are skipped.'
      : null,
    draftConfig.enabled && normalizedUserAgents.length === 0
      ? 'User-Agent pool is empty, so Playwright will keep its browser default user agent.'
      : null,
    !draftConfig.spoof_timezone.trim()
      ? 'Timezone spoofing is blank, so no Playwright timezone_id will be applied.'
      : null,
  ].filter((entry): entry is string => Boolean(entry));

  return (
    <div className="stealth-page">
      <PageHeader
        eyebrow="Browser Fingerprint"
        title="Stealth"
        description="Runtime policy for browser fingerprint reduction. These settings are persisted in the plugin runtime overlay and applied to the next Playwright BrowserContext."
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
              onClick={() => mutation.mutate(draftConfig)}
            >
              <Icon path={paths.check} size={14} />
              {mutation.isPending ? 'Saving…' : 'Save policy'}
            </button>
          </div>
        }
      />

      <Panel className="stealth-hero-panel">
        <div className="stealth-hero">
          <div className="stealth-hero-copy">
            <span className="badge muted">runtime overlay</span>
            <h2>Session fingerprint policy</h2>
            <p>
              This tab controls how Baselithbot shapes each new Playwright BrowserContext: identity
              selection, locale/timezone signals, webdriver masking, and the extra stealth layer
              applied through init scripts and `playwright-stealth`.
            </p>

            <div className="chip-row">
              <span className={`badge ${draftConfig.enabled ? 'ok' : 'err'}`}>
                {draftConfig.enabled ? 'stealth active' : 'stealth bypassed'}
              </span>
              <span className={`badge ${draftConfig.rotate_user_agent ? 'warn' : 'muted'}`}>
                {draftConfig.rotate_user_agent ? 'rotating identity' : 'stable identity'}
              </span>
              <span className={`badge ${draftConfig.mask_webdriver ? 'ok' : 'warn'}`}>
                {draftConfig.mask_webdriver ? 'webdriver masked' : 'webdriver exposed'}
              </span>
              <span className={`badge ${isDirty ? 'warn' : 'muted'}`}>
                {isDirty ? 'unsaved changes' : 'saved'}
              </span>
            </div>

            <div className="stealth-hero-metrics">
              <div className="stealth-hero-metric">
                <span className="meta-label">User-Agent strategy</span>
                <strong>{draftConfig.rotate_user_agent ? 'Rotating' : 'Deterministic'}</strong>
                <span className="muted">{activeUserAgent(draftConfig)}</span>
              </div>
              <div className="stealth-hero-metric">
                <span className="meta-label">Locale profile</span>
                <strong>{normalizedLanguages[0] || 'Unset'}</strong>
                <span className="muted">
                  {normalizedLanguages.length > 0
                    ? `${normalizedLanguages.join(', ')}`
                    : 'No navigator.language override'}
                </span>
              </div>
              <div className="stealth-hero-metric">
                <span className="meta-label">Timezone</span>
                <strong>{draftConfig.spoof_timezone.trim() || 'Unset'}</strong>
                <span className="muted">
                  {draftConfig.enabled
                    ? 'Applied via Playwright timezone_id'
                    : 'Ignored while stealth is disabled'}
                </span>
              </div>
            </div>
          </div>

          <div className="stealth-switch-card">
            <div className="stealth-switch-head">
              <span className="meta-label">Stealth master</span>
              <span className={`badge ${draftConfig.enabled ? 'ok' : 'err'}`}>
                {draftConfig.enabled ? 'enabled' : 'disabled'}
              </span>
            </div>

            <label className="stealth-switch">
              <input
                type="checkbox"
                checked={draftConfig.enabled}
                onChange={(event) => update('enabled', event.target.checked)}
              />
              <span className="stealth-switch-copy">
                <strong>
                  {draftConfig.enabled ? 'Stealth protections armed' : 'Default browser path'}
                </strong>
                <span>
                  When enabled, Baselithbot applies BrowserContext options, extra headers, init
                  scripts, and the optional `playwright-stealth` layer before interacting with the
                  page.
                </span>
              </span>
            </label>

            <div className="stealth-switch-meta">
              <div className="stealth-kv">
                <span>Agent rebuild</span>
                <span>Required after save</span>
              </div>
              <div className="stealth-kv">
                <span>Context directives</span>
                <span>{formatNumber(contextPreview.length + headers.length)}</span>
              </div>
              <div className="stealth-kv">
                <span>User-Agent pool</span>
                <span>{formatNumber(normalizedUserAgents.length)} configured</span>
              </div>
            </div>
          </div>
        </div>
      </Panel>

      <section className="grid grid-cols-4">
        <StatCard
          label="Toggles On"
          value={`${enabledToggles.length}/${TOGGLE_FIELDS.length}`}
          sub="stealth controls active"
          iconPath={paths.shield}
          accent="teal"
        />
        <StatCard
          label="UA Pool"
          value={formatNumber(normalizedUserAgents.length)}
          sub={draftConfig.rotate_user_agent ? 'rotation candidates' : 'configured identities'}
          iconPath={paths.refresh}
          accent="amber"
        />
        <StatCard
          label="Locales"
          value={formatNumber(normalizedLanguages.length)}
          sub="header + navigator override set"
          iconPath={paths.sparkles}
          accent="violet"
        />
        <StatCard
          label="Timezone"
          value={draftConfig.spoof_timezone.trim() || 'Unset'}
          sub={draftConfig.enabled ? 'Playwright context setting' : 'inactive while disabled'}
          iconPath={paths.clock}
          accent={draftConfig.spoof_timezone.trim() ? 'cyan' : 'rose'}
        />
      </section>

      <section className="grid grid-split-2-1">
        <Panel
          title="Countermeasure matrix"
          tag={`${formatNumber(enabledToggles.length)} active`}
          className="stealth-matrix-panel"
        >
          <div className="stealth-matrix-grid">
            {TOGGLE_FIELDS.map((field) => {
              const enabled = draftConfig[field.key];
              return (
                <article
                  key={field.key}
                  className={['stealth-matrix-card', enabled ? 'is-enabled' : ''].join(' ')}
                >
                  <div className="stealth-matrix-head">
                    <div className="stealth-matrix-icon">
                      <Icon path={paths[field.icon]} size={16} />
                    </div>
                    <span
                      className={`badge ${
                        enabled ? (field.accent === 'amber' ? 'warn' : 'ok') : 'muted'
                      }`}
                    >
                      {enabled ? 'enabled' : 'disabled'}
                    </span>
                  </div>

                  <div className="stealth-matrix-body">
                    <div>
                      <div className="stealth-matrix-title-row">
                        <strong>{field.label}</strong>
                      </div>
                      <p>{field.description}</p>
                    </div>

                    <label className="stealth-toggle-row">
                      <span className="meta-label">Apply control</span>
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={(event) => update(field.key, event.target.checked)}
                      />
                    </label>

                    <div className="stealth-chip-list">
                      {field.outcomes.map((outcome) => (
                        <span key={outcome} className="stealth-chip">
                          {outcome}
                        </span>
                      ))}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </Panel>

        <Panel title="Coverage review" tag="before save">
          <div className="stack-section">
            <div className="detail-grid">
              <div className="meta-tile">
                <span className="meta-label">Master</span>
                <span>{draftConfig.enabled ? 'On' : 'Off'}</span>
              </div>
              <div className="meta-tile">
                <span className="meta-label">Rotation</span>
                <span>{draftConfig.rotate_user_agent ? 'Randomized' : 'Fixed'}</span>
              </div>
              <div className="meta-tile">
                <span className="meta-label">Languages</span>
                <span>{normalizedLanguages.length > 0 ? normalizedLanguages[0] : 'None'}</span>
              </div>
              <div className="meta-tile">
                <span className="meta-label">Timezone</span>
                <span>{draftConfig.spoof_timezone.trim() || 'None'}</span>
              </div>
            </div>

            {warnings.length === 0 ? (
              <div className="info-block">
                Stealth policy is coherent: the active toggles have enough data to produce a
                consistent BrowserContext fingerprint profile.
              </div>
            ) : (
              <div className="stealth-warning-list">
                {warnings.map((warning) => (
                  <div key={warning} className="stealth-warning-item">
                    <Icon path={paths.shieldOff} size={14} />
                    <span>{warning}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="stealth-policy-note">
              `playwright-stealth` remains additive: this dashboard config now applies the core
              BrowserContext signals directly, even before the external stealth package runs.
            </div>
          </div>
        </Panel>
      </section>

      <section className="grid grid-split-2-1">
        <Panel title="Identity profile" tag="locale + timezone">
          <div className="stealth-form-grid">
            <label className="form-row">
              <span>Languages</span>
              <input
                type="text"
                className="input mono"
                value={langDraft}
                onChange={(event) => setLangDraft(event.target.value)}
                placeholder="en-US, en"
              />
            </label>

            <label className="form-row">
              <span>Timezone</span>
              <input
                type="text"
                className="input mono"
                value={draftConfig.spoof_timezone}
                onChange={(event) => update('spoof_timezone', event.target.value)}
                placeholder="UTC"
              />
            </label>
          </div>

          <div className="stealth-preview-grid">
            <div className="meta-tile">
              <span className="meta-label">Accept-Language</span>
              <span>{headers[0]?.value || 'Not applied'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">navigator.language</span>
              <span>{normalizedLanguages[0] || 'Not overridden'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">navigator.languages</span>
              <span>
                {normalizedLanguages.length > 0 ? normalizedLanguages.join(', ') : 'None'}
              </span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">timezone_id</span>
              <span>{draftConfig.spoof_timezone.trim() || 'Not applied'}</span>
            </div>
          </div>
        </Panel>

        <Panel title="Applied layers" tag={`${formatNumber(contextPreview.length)} context opts`}>
          <div className="stack-section">
            <div>
              <div className="section-label">BrowserContext options</div>
              {contextPreview.length === 0 ? (
                <div className="info-block">
                  No BrowserContext spoofing options will be applied.
                </div>
              ) : (
                <div className="stealth-surface-list">
                  {contextPreview.map((entry) => (
                    <div key={entry.label} className="stealth-surface-item">
                      <span className="mono">{entry.label}</span>
                      <span>{entry.value}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div>
              <div className="section-label">Header + JS mutations</div>
              <div className="stealth-chip-list">
                <span className="stealth-chip">
                  {headers.length > 0
                    ? `Accept-Language: ${headers[0].value}`
                    : 'No header override'}
                </span>
                <span className="stealth-chip">
                  {draftConfig.mask_webdriver
                    ? 'navigator.webdriver hidden'
                    : 'navigator.webdriver untouched'}
                </span>
                <span className="stealth-chip">WebGL vendor script mutation</span>
                <span className="stealth-chip">navigator.plugins synthetic stub</span>
              </div>
            </div>
          </div>
        </Panel>
      </section>

      <Panel title="User-Agent pool" tag={`${formatNumber(normalizedUserAgents.length)} entries`}>
        <div className="stealth-ua-panel">
          <div className="stealth-ua-copy">
            <div className="section-label">Selection strategy</div>
            <div className="stealth-ua-title">
              {normalizedUserAgents.length === 0
                ? 'Browser default user agent'
                : draftConfig.rotate_user_agent
                  ? 'Randomized per session'
                  : 'Pinned to first entry'}
            </div>
            <div className="stealth-field-copy">
              When stealth is enabled, the selected user agent is now applied directly to the
              Playwright BrowserContext, not just to request headers.
            </div>
            <div className="info-block">
              Active profile: <span className="mono">{activeUserAgent(draftConfig)}</span>
            </div>
          </div>

          <textarea
            className="textarea stealth-ua-textarea"
            value={uaDraft}
            onChange={(event) => setUaDraft(event.target.value)}
            placeholder={
              'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36\nMozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
          />
        </div>
      </Panel>
    </div>
  );
}
