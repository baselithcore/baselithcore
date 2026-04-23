import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '../../components/PageHeader';
import { Skeleton } from '../../components/Skeleton';
import { useToasts } from '../../components/ToastProvider';
import { api, type StealthConfig } from '../../lib/api';
import { Icon, paths } from '../../lib/icons';
import {
  browserContextPreview,
  headerPreview,
  normalizeLanguagesDraft,
  normalizeUserAgentsDraft,
  sameConfig,
  TOGGLE_FIELDS,
} from './helpers';
import { HeroPanel } from './sections/HeroPanel';
import { StatsRow } from './sections/StatsRow';
import { CountermeasureMatrix } from './sections/CountermeasureMatrix';
import { CoverageReview } from './sections/CoverageReview';
import { IdentityProfile } from './sections/IdentityProfile';
import { AppliedLayers } from './sections/AppliedLayers';
import { UserAgentPool } from './sections/UserAgentPool';

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

      <HeroPanel
        draftConfig={draftConfig}
        isDirty={isDirty}
        normalizedLanguages={normalizedLanguages}
        normalizedUserAgents={normalizedUserAgents}
        contextPreview={contextPreview}
        headers={headers}
        update={update}
      />

      <StatsRow
        draftConfig={draftConfig}
        enabledTogglesCount={enabledToggles.length}
        normalizedLanguages={normalizedLanguages}
        normalizedUserAgents={normalizedUserAgents}
      />

      <section className="grid grid-split-2-1">
        <CountermeasureMatrix
          draftConfig={draftConfig}
          enabledTogglesCount={enabledToggles.length}
          update={update}
        />

        <CoverageReview
          draftConfig={draftConfig}
          normalizedLanguages={normalizedLanguages}
          warnings={warnings}
        />
      </section>

      <section className="grid grid-split-2-1">
        <IdentityProfile
          draftConfig={draftConfig}
          langDraft={langDraft}
          setLangDraft={setLangDraft}
          normalizedLanguages={normalizedLanguages}
          headers={headers}
          update={update}
        />

        <AppliedLayers
          draftConfig={draftConfig}
          contextPreview={contextPreview}
          headers={headers}
        />
      </section>

      <UserAgentPool
        draftConfig={draftConfig}
        uaDraft={uaDraft}
        setUaDraft={setUaDraft}
        normalizedUserAgents={normalizedUserAgents}
      />
    </div>
  );
}
