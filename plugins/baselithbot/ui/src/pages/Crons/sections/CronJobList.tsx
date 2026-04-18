import type { CronCatalog, CronJob, CustomCronPayload } from '../../../lib/api';
import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import { formatAbsolute, formatNumber } from '../../../lib/format';
import type { SortKey, StatusFilter } from '../helpers';
import { CustomCronForm } from './CustomCronForm';

interface CronJobListProps {
  jobs: CronJob[];
  filtered: CronJob[];
  search: string;
  onSearchChange: (value: string) => void;
  status: StatusFilter;
  onStatusChange: (value: StatusFilter) => void;
  sort: SortKey;
  onSortChange: (value: SortKey) => void;
  formOpen: boolean;
  onToggleForm: () => void;
  catalog: CronCatalog | undefined;
  createPending: boolean;
  onCreate: (payload: CustomCronPayload) => void;
  onCancelForm: () => void;
  onSelect: (name: string) => void;
}

export function CronJobList({
  jobs,
  filtered,
  search,
  onSearchChange,
  status,
  onStatusChange,
  sort,
  onSortChange,
  formOpen,
  onToggleForm,
  catalog,
  createPending,
  onCreate,
  onCancelForm,
  onSelect,
}: CronJobListProps) {
  return (
    <Panel>
      <div className="toolbar">
        <input
          className="input toolbar-grow"
          placeholder="Filter jobs…"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
        />
        <select
          className="select"
          value={status}
          onChange={(event) => onStatusChange(event.target.value as StatusFilter)}
        >
          <option value="all">all statuses</option>
          <option value="enabled">enabled</option>
          <option value="paused">paused</option>
          <option value="error">error only</option>
        </select>
        <select
          className="select"
          value={sort}
          onChange={(event) => onSortChange(event.target.value as SortKey)}
        >
          <option value="next">sort: next run</option>
          <option value="runs">sort: runs</option>
          <option value="name">sort: name</option>
        </select>
        <button type="button" className="btn" onClick={onToggleForm}>
          {formOpen ? 'Close form' : 'New custom job'}
        </button>
      </div>

      {formOpen && catalog && (
        <CustomCronForm
          actions={catalog.actions}
          namePrefix={catalog.name_prefix}
          submitting={createPending}
          onSubmit={onCreate}
          onCancel={onCancelForm}
        />
      )}

      {jobs.length === 0 ? (
        <EmptyState
          title="No cron jobs scheduled"
          description="Default maintenance jobs register at plugin startup. Use 'New custom job' to add your own."
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No cron jobs match"
          description="Adjust the filters to inspect another scheduled job."
        />
      ) : (
        <div className="cards-grid">
          {filtered.map((job) => (
            <button
              key={job.name}
              type="button"
              className="record-card"
              onClick={() => onSelect(job.name)}
            >
              <div className="record-card-head">
                <div className="record-card-title mono">{job.name}</div>
                <span className={`badge ${job.last_error ? 'err' : job.enabled ? 'ok' : 'muted'}`}>
                  {job.last_error ? 'error' : job.enabled ? 'enabled' : 'paused'}
                </span>
              </div>
              <div className="record-card-meta">
                <div className="record-kv">
                  <span>Interval</span>
                  <span className="mono">{job.interval_seconds}s</span>
                </div>
                <div className="record-kv">
                  <span>Runs</span>
                  <span className="mono">{formatNumber(job.runs)}</span>
                </div>
                <div className="record-kv">
                  <span>Next run</span>
                  <span className="mono">{formatAbsolute(job.next_run_at)}</span>
                </div>
                <div className="record-kv">
                  <span>Kind</span>
                  <span>{job.custom ? 'Custom' : 'System'}</span>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </Panel>
  );
}
