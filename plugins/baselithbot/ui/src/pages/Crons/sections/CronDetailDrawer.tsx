import type { CronJob } from '../../../lib/api';
import { DetailDrawer } from '../../../components/DetailDrawer';
import { Icon, paths } from '../../../lib/icons';
import { formatAbsolute, formatNumber } from '../../../lib/format';
import { MetaTile } from '../components';

interface CronDetailDrawerProps {
  selected: CronJob | null;
  intervalDraft: string;
  onIntervalDraftChange: (value: string) => void;
  drawerBusy: boolean;
  onClose: () => void;
  onToggle: (name: string, enabled: boolean) => void;
  onRunNow: (name: string) => void;
  onUpdateInterval: (name: string, seconds: number) => void;
  onRemove: (selected: CronJob) => void;
}

export function CronDetailDrawer({
  selected,
  intervalDraft,
  onIntervalDraftChange,
  drawerBusy,
  onClose,
  onToggle,
  onRunNow,
  onUpdateInterval,
  onRemove,
}: CronDetailDrawerProps) {
  return (
    <DetailDrawer
      open={!!selected}
      title={selected?.name ?? ''}
      subtitle={selected?.custom ? 'Custom cron job' : 'Cron job details'}
      onClose={onClose}
    >
      {selected && (
        <>
          <div className="detail-grid">
            <MetaTile label="Interval" value={`${selected.interval_seconds}s`} />
            <MetaTile label="Runs" value={formatNumber(selected.runs)} />
            <MetaTile label="Next run" value={formatAbsolute(selected.next_run_at)} />
            <MetaTile
              label="Last run"
              value={selected.last_run_at ? formatAbsolute(selected.last_run_at) : '—'}
            />
            <MetaTile
              label="Status"
              value={selected.last_error ? 'error' : selected.enabled ? 'enabled' : 'paused'}
            />
            <MetaTile label="Kind" value={selected.custom ? 'custom' : 'system'} />
          </div>

          {selected.description && (
            <div className="stack-section">
              <div className="section-label">Description</div>
              <div className="info-block">{selected.description}</div>
            </div>
          )}

          <div className="stack-section">
            <div className="section-label">Operational summary</div>
            <div className="info-block">
              {selected.last_error
                ? 'The scheduler is keeping this job registered, but the last execution surfaced an error and needs inspection.'
                : selected.enabled
                  ? 'This cron job is enabled and queued for its next periodic execution.'
                  : 'This cron job is registered but currently paused.'}
            </div>
          </div>

          {selected.last_error && (
            <div className="stack-section">
              <div className="section-label">Last error</div>
              <pre className="code-block">{selected.last_error}</pre>
            </div>
          )}

          <div className="stack-section">
            <div className="section-label">Controls</div>
            <div className="toolbar" style={{ flexWrap: 'wrap', gap: 8 }}>
              <button
                type="button"
                className="btn"
                disabled={drawerBusy}
                onClick={() => onToggle(selected.name, !selected.enabled)}
              >
                {selected.enabled ? 'Pause' : 'Resume'}
              </button>
              <button
                type="button"
                className="btn"
                disabled={drawerBusy || !selected.enabled}
                onClick={() => onRunNow(selected.name)}
              >
                Run now
              </button>
            </div>
          </div>

          <div className="stack-section">
            <div className="section-label">Interval (seconds)</div>
            <div className="toolbar" style={{ gap: 8 }}>
              <input
                className="input"
                type="number"
                min={1}
                max={86400}
                step={1}
                value={intervalDraft}
                onChange={(event) => onIntervalDraftChange(event.target.value)}
              />
              <button
                type="button"
                className="btn"
                disabled={
                  drawerBusy ||
                  !intervalDraft ||
                  Number.isNaN(Number(intervalDraft)) ||
                  Number(intervalDraft) === selected.interval_seconds ||
                  Number(intervalDraft) < 1
                }
                onClick={() => onUpdateInterval(selected.name, Number(intervalDraft))}
              >
                Update interval
              </button>
            </div>
          </div>

          <button
            type="button"
            className="btn danger"
            disabled={drawerBusy}
            onClick={() => onRemove(selected)}
          >
            <Icon path={paths.trash} size={14} />
            Remove job
          </button>
        </>
      )}
    </DetailDrawer>
  );
}
