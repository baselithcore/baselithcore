import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute } from '../lib/format';

export function Crons() {
  const qc = useQueryClient();
  const { push } = useToasts();
  const { data, isLoading } = useQuery({
    queryKey: ['crons'],
    queryFn: api.crons,
    refetchInterval: 5_000,
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.removeCron(name),
    onSuccess: (_, name) => {
      qc.invalidateQueries({ queryKey: ['crons'] });
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Scheduler"
        title="Cron jobs"
        description={`Backend: ${data?.backend ?? '—'}. Interval-based periodic jobs run inside the plugin.`}
      />

      {isLoading && <Skeleton height={220} />}

      {data && data.jobs.length === 0 && (
        <EmptyState
          title="No cron jobs scheduled"
          description="Register jobs via the CronScheduler to surface them here."
        />
      )}

      {data && data.jobs.length > 0 && (
        <Panel padded={false}>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 220 }}>Job</th>
                <th>Interval</th>
                <th>Runs</th>
                <th>Next run</th>
                <th>Status</th>
                <th style={{ width: 120 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((j) => (
                <tr key={j.name}>
                  <td>
                    <span className="mono" style={{ color: 'var(--ink-100)' }}>
                      {j.name}
                    </span>
                  </td>
                  <td className="mono">{j.interval_seconds}s</td>
                  <td className="mono">{j.runs}</td>
                  <td className="mono muted">{formatAbsolute(j.next_run_at)}</td>
                  <td>
                    {j.last_error ? (
                      <span className="badge err" title={j.last_error}>
                        error
                      </span>
                    ) : j.enabled ? (
                      <span className="badge ok">enabled</span>
                    ) : (
                      <span className="badge muted">paused</span>
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn danger xs"
                      disabled={remove.isPending}
                      onClick={() => {
                        if (!window.confirm(`Remove cron job "${j.name}" from the scheduler?`)) {
                          return;
                        }
                        remove.mutate(j.name);
                      }}
                    >
                      <Icon path={paths.trash} size={12} />
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}
