import type { ReplayRun } from '../../../lib/api';
import { formatAbsolute, formatNumber, formatRelative, truncate } from '../../../lib/format';
import { Icon, paths } from '../../../lib/icons';
import { StepStrip } from '../components';
import { lastKnownUrl } from '../helpers';

export function StepViewer({
  run,
  stepIndex,
  followLive,
  onFollowLiveChange,
  onSelectStep,
}: {
  run: ReplayRun;
  stepIndex: number;
  followLive: boolean;
  onFollowLiveChange: (value: boolean) => void;
  onSelectStep: (index: number) => void;
}) {
  const safeIndex = Math.min(Math.max(stepIndex, 0), Math.max(run.steps.length - 1, 0));
  const step = run.steps[safeIndex] ?? null;
  const screenshotSrc = step?.screenshot_b64
    ? `data:image/png;base64,${step.screenshot_b64}`
    : null;
  const lastUrl = lastKnownUrl(run);

  return (
    <div className="replay-detail-stack">
      <div className="replay-step-toolbar">
        <div className="replay-step-controls">
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              onFollowLiveChange(false);
              onSelectStep(Math.max(0, safeIndex - 1));
            }}
            disabled={safeIndex === 0}
          >
            ◀ Prev
          </button>
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              onFollowLiveChange(false);
              onSelectStep(Math.min(run.steps.length - 1, safeIndex + 1));
            }}
            disabled={safeIndex >= run.steps.length - 1}
          >
            Next ▶
          </button>
          <span className="badge muted">
            step {safeIndex + 1} / {run.steps.length}
          </span>
        </div>

        <div className="replay-step-actions">
          <button
            type="button"
            className={`btn ghost sm ${followLive ? 'is-live' : ''}`}
            onClick={() => onFollowLiveChange(!followLive)}
          >
            <Icon path={paths.activity} size={14} />
            {followLive ? 'Following live' : 'Follow live'}
          </button>
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              onFollowLiveChange(false);
              onSelectStep(run.steps.length - 1);
            }}
          >
            Jump to latest
          </button>
        </div>
      </div>

      <div className="replay-stage">
        <div className="replay-stage-visual">
          {screenshotSrc ? (
            <img
              src={screenshotSrc}
              alt={`Replay screenshot for step ${safeIndex + 1}`}
              className="replay-stage-image"
            />
          ) : (
            <div className="replay-stage-empty">No screenshot was captured for this step.</div>
          )}
        </div>

        <div className="replay-stage-sidebar">
          <div className="detail-grid">
            <div className="meta-tile">
              <span className="meta-label">Action</span>
              <span className="mono">{step?.action || '—'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Captured</span>
              <span>{step ? formatRelative(step.ts) : '—'}</span>
              <span className="muted">{step ? formatAbsolute(step.ts) : '—'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">URL</span>
              <span>{step?.current_url ? truncate(step.current_url, 44) : '—'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Extracted keys</span>
              <span>{formatNumber(Object.keys(step?.extracted_data ?? {}).length)}</span>
            </div>
          </div>

          <div className="replay-copy-block">
            <span className="section-label">Reasoning</span>
            <div className="info-block">
              {step?.reasoning || 'No reasoning persisted for this step.'}
            </div>
          </div>

          <div className="replay-copy-block">
            <span className="section-label">Current URL</span>
            <div className="info-block mono replay-url-block">
              {step?.current_url || lastUrl || 'No URL available'}
            </div>
          </div>

          <div className="replay-copy-block">
            <span className="section-label">Extracted data at this step</span>
            <pre className="code-block">{JSON.stringify(step?.extracted_data ?? {}, null, 2)}</pre>
          </div>
        </div>
      </div>

      <StepStrip
        run={run}
        selectedStep={safeIndex}
        onSelect={(index) => {
          onFollowLiveChange(false);
          onSelectStep(index);
        }}
      />
    </div>
  );
}
