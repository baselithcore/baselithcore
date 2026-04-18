import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useConfirm } from '../../components/ConfirmProvider';
import { PageHeader } from '../../components/PageHeader';
import { Skeleton } from '../../components/Skeleton';
import { useToasts } from '../../components/ToastProvider';
import { api, type ComputerUseConfig } from '../../lib/api';
import { Icon, paths } from '../../lib/icons';
import {
  CAPABILITY_FIELDS,
  normalizeAllowlistDraft,
  sameConfig,
} from './helpers';
import { HeroSection } from './sections/HeroSection';
import { StatsSection } from './sections/StatsSection';
import { CapabilityMatrixSection } from './sections/CapabilityMatrixSection';
import { RiskReviewSection } from './sections/RiskReviewSection';
import { GuardrailsSection } from './sections/GuardrailsSection';
import { ToolSurfaceSection } from './sections/ToolSurfaceSection';

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

      <HeroSection
        draftConfig={draftConfig}
        privilegedEnabled={privilegedEnabled}
        auditConfigured={auditConfigured}
        filesystemConfigured={filesystemConfigured}
        isDirty={isDirty}
        enabledToolCount={enabledToolCount}
        normalizedAllowlist={normalizedAllowlist}
        update={update}
      />

      <StatsSection
        draftConfig={draftConfig}
        enabledCapabilities={enabledCapabilities}
        enabledToolCount={enabledToolCount}
        normalizedAllowlist={normalizedAllowlist}
        auditConfigured={auditConfigured}
      />

      <section className="grid grid-split-2-1">
        <CapabilityMatrixSection
          draftConfig={draftConfig}
          enabledCapabilities={enabledCapabilities}
          update={update}
        />

        <RiskReviewSection
          draftConfig={draftConfig}
          auditConfigured={auditConfigured}
          warnings={warnings}
        />
      </section>

      <GuardrailsSection
        draftConfig={draftConfig}
        allowlistDraft={allowlistDraft}
        setAllowlistDraft={setAllowlistDraft}
        normalizedAllowlist={normalizedAllowlist}
        shellConfigured={shellConfigured}
        update={update}
      />

      <ToolSurfaceSection
        enabledCapabilities={enabledCapabilities}
        enabledToolCount={enabledToolCount}
      />
    </div>
  );
}
