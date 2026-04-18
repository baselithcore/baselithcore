import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CronJob, type CustomCronPayload } from '../../lib/api';
import { useConfirm } from '../../components/ConfirmProvider';
import { PageHeader } from '../../components/PageHeader';
import { Skeleton } from '../../components/Skeleton';
import { useToasts } from '../../components/ToastProvider';
import { formatNumber } from '../../lib/format';
import type { SortKey, StatusFilter } from './helpers';
import { CronJobList } from './sections/CronJobList';
import { CronDetailDrawer } from './sections/CronDetailDrawer';

export function Crons() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { push } = useToasts();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [sort, setSort] = useState<SortKey>('next');
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [intervalDraft, setIntervalDraft] = useState<string>('');
  const [formOpen, setFormOpen] = useState(false);
  const deferredSearch = useDeferredValue(search);

  const { data, isLoading } = useQuery({
    queryKey: ['crons'],
    queryFn: api.crons,
    refetchInterval: 5_000,
  });

  const { data: catalog } = useQuery({
    queryKey: ['crons', 'catalog'],
    queryFn: api.cronCatalog,
    staleTime: 60_000,
  });

  const jobs = data?.jobs ?? [];
  const selected = useMemo(
    () => jobs.find((job) => job.name === selectedName) ?? null,
    [jobs, selectedName]
  );
  const enabledCount = useMemo(() => jobs.filter((job) => job.enabled).length, [jobs]);
  const errorCount = useMemo(() => jobs.filter((job) => !!job.last_error).length, [jobs]);

  useEffect(() => {
    if (selected) setIntervalDraft(String(selected.interval_seconds));
  }, [selected?.name, selected?.interval_seconds]);

  const filtered = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return jobs
      .filter((job) => {
        if (status === 'enabled' && !job.enabled) return false;
        if (status === 'paused' && job.enabled) return false;
        if (status === 'error' && !job.last_error) return false;
        if (!needle) return true;
        return (
          job.name.toLowerCase().includes(needle) ||
          String(job.interval_seconds).includes(needle) ||
          (job.description || '').toLowerCase().includes(needle) ||
          (job.last_error || '').toLowerCase().includes(needle)
        );
      })
      .sort((left, right) => {
        if (sort === 'name') return left.name.localeCompare(right.name);
        if (sort === 'runs') return right.runs - left.runs || left.name.localeCompare(right.name);
        return left.next_run_at - right.next_run_at || left.name.localeCompare(right.name);
      });
  }, [deferredSearch, jobs, sort, status]);

  const invalidate = () => qc.invalidateQueries({ queryKey: ['crons'] });

  const remove = useMutation({
    mutationFn: (name: string) => api.removeCron(name),
    onSuccess: (_, name) => {
      setSelectedName((current) => (current === name ? null : current));
      invalidate();
      push({
        tone: 'success',
        title: 'Cron removed',
        description: `${name} was removed from the scheduler.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Cron removal failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const toggle = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.toggleCron(name, enabled),
    onSuccess: (_, { name, enabled }) => {
      invalidate();
      push({
        tone: 'success',
        title: enabled ? 'Cron resumed' : 'Cron paused',
        description: `${name} is now ${enabled ? 'enabled' : 'paused'}.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Cron toggle failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const runNow = useMutation({
    mutationFn: (name: string) => api.runCron(name),
    onSuccess: (_, name) => {
      invalidate();
      push({
        tone: 'success',
        title: 'Cron triggered',
        description: `${name} will run on the next scheduler tick.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Cron trigger failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const updateInterval = useMutation({
    mutationFn: ({ name, seconds }: { name: string; seconds: number }) =>
      api.updateCronInterval(name, seconds),
    onSuccess: (_, { name, seconds }) => {
      invalidate();
      push({
        tone: 'success',
        title: 'Cron interval updated',
        description: `${name} → every ${seconds}s.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Interval update failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const create = useMutation({
    mutationFn: (payload: CustomCronPayload) => api.createCustomCron(payload),
    onSuccess: (_, payload) => {
      invalidate();
      setFormOpen(false);
      push({
        tone: 'success',
        title: 'Custom cron registered',
        description: `${payload.name} is now scheduled every ${payload.interval_seconds}s.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Custom cron create failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const drawerBusy =
    toggle.isPending || runNow.isPending || updateInterval.isPending || remove.isPending;

  const handleRemove = async (job: CronJob) => {
    if (
      !(await confirm({
        title: 'Remove cron job',
        description: `The job "${job.name}" will be removed from the scheduler.`,
        confirmLabel: 'Remove job',
        tone: 'danger',
      }))
    ) {
      return;
    }
    remove.mutate(job.name);
  };

  if (isLoading || !data) return <Skeleton height={280} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Scheduler"
        title="Cron jobs"
        description={`Backend: ${data.backend} · ${formatNumber(jobs.length)} jobs · ${formatNumber(enabledCount)} enabled · ${formatNumber(errorCount)} with errors.`}
      />

      <CronJobList
        jobs={jobs}
        filtered={filtered}
        search={search}
        onSearchChange={setSearch}
        status={status}
        onStatusChange={setStatus}
        sort={sort}
        onSortChange={setSort}
        formOpen={formOpen}
        onToggleForm={() => setFormOpen((open) => !open)}
        catalog={catalog}
        createPending={create.isPending}
        onCreate={(payload) => create.mutate(payload)}
        onCancelForm={() => setFormOpen(false)}
        onSelect={setSelectedName}
      />

      <CronDetailDrawer
        selected={selected}
        intervalDraft={intervalDraft}
        onIntervalDraftChange={setIntervalDraft}
        drawerBusy={drawerBusy}
        onClose={() => setSelectedName(null)}
        onToggle={(name, enabled) => toggle.mutate({ name, enabled })}
        onRunNow={(name) => runNow.mutate(name)}
        onUpdateInterval={(name, seconds) => updateInterval.mutate({ name, seconds })}
        onRemove={handleRemove}
      />
    </div>
  );
}
