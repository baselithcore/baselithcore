import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { EmptyState } from '../../components/EmptyState';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { Skeleton } from '../../components/Skeleton';
import { StatCard } from '../../components/StatCard';
import { useToasts } from '../../components/ToastProvider';
import { api } from '../../lib/api';
import { formatRelative } from '../../lib/format';
import { Icon, paths } from '../../lib/icons';
import { CAPABILITIES, EXPECTED_TOOL_NAMES, type CapabilityKey } from './constants';
import {
  capabilityChecklist,
  exportedToolNames,
  summarizeResult,
  type RunLogEntry,
} from './helpers';
import { FilesystemSection } from './sections/Filesystem';
import { GoalRunSection } from './sections/GoalRun';
import { HeroCatalogSection } from './sections/HeroCatalog';
import { InspectorHistorySection } from './sections/InspectorHistory';
import { KeyboardSection } from './sections/Keyboard';
import { ScreenPointerSection } from './sections/ScreenPointer';
import { ShellSection } from './sections/Shell';
import type { DesktopShared } from './shared';

export function DesktopTask() {
  const qc = useQueryClient();
  const { push } = useToasts();

  const catalog = useQuery({
    queryKey: ['desktopTools'],
    queryFn: api.desktopTools,
    refetchInterval: 15_000,
  });

  const [runLog, setRunLog] = useState<RunLogEntry[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

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

  const shared: DesktopShared = {
    policy,
    tools,
    toolMap,
    canUse,
    invoke,
    invokePending: invokeMutation.isPending,
    launcherBinary,
  };

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

      <GoalRunSection policy={policy} />

      <HeroCatalogSection
        policy={policy}
        tools={tools}
        toolNames={toolNames}
        enabledCapabilities={enabledCapabilities}
        gatedCapabilities={gatedCapabilities}
        missingExpectedTools={missingExpectedTools}
        ready={ready}
        policyMessages={policyMessages}
      />

      <section className="grid grid-split-2-1">
        <div className="desktop-stack">
          <ScreenPointerSection shared={shared} />
          <KeyboardSection shared={shared} />
          <ShellSection shared={shared} />
          <FilesystemSection shared={shared} />
        </div>

        <InspectorHistorySection
          runLog={runLog}
          selectedEntry={selectedEntry}
          selectedTool={selectedTool}
          screenshotBase64={screenshotBase64}
          onSelect={setSelectedRunId}
        />
      </section>
    </div>
  );
}
