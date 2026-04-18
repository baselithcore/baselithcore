import { Panel } from '../../../components/Panel';
import type { StealthConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { Icon, paths } from '../../../lib/icons';
import { TOGGLE_FIELDS } from '../helpers';

type CountermeasureMatrixProps = {
  draftConfig: StealthConfig;
  enabledTogglesCount: number;
  update: <K extends keyof StealthConfig>(key: K, value: StealthConfig[K]) => void;
};

export function CountermeasureMatrix({
  draftConfig,
  enabledTogglesCount,
  update,
}: CountermeasureMatrixProps) {
  return (
    <Panel
      title="Countermeasure matrix"
      tag={`${formatNumber(enabledTogglesCount)} active`}
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
  );
}
