import { Panel } from '../../../components/Panel';
import { type ComputerUseConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { Icon, paths } from '../../../lib/icons';
import { CAPABILITY_FIELDS, capabilityTone, type CapabilitySpec } from '../helpers';

type CapabilityMatrixSectionProps = {
  draftConfig: ComputerUseConfig;
  enabledCapabilities: CapabilitySpec[];
  update: <K extends keyof ComputerUseConfig>(key: K, value: ComputerUseConfig[K]) => void;
};

export function CapabilityMatrixSection({
  draftConfig,
  enabledCapabilities,
  update,
}: CapabilityMatrixSectionProps) {
  return (
    <Panel
      title="Capability matrix"
      tag={`${formatNumber(enabledCapabilities.length)} active`}
      className="computer-capability-panel"
    >
      <div className="computer-capability-grid">
        {CAPABILITY_FIELDS.map((field) => {
          const enabled = draftConfig[field.key];
          return (
            <article
              key={field.key}
              className={[
                'computer-capability-card',
                enabled ? 'is-enabled' : '',
                field.danger ? 'is-danger' : '',
              ]
                .filter(Boolean)
                .join(' ')}
            >
              <div className="computer-capability-head">
                <div className="computer-capability-icon">
                  <Icon path={paths[field.icon]} size={16} />
                </div>
                <span className={`badge ${enabled ? capabilityTone(field.accent) : 'muted'}`}>
                  {enabled ? 'enabled' : 'disabled'}
                </span>
              </div>

              <div className="computer-capability-body">
                <div>
                  <div className="computer-capability-title-row">
                    <strong>{field.label}</strong>
                    {field.danger && <span className="badge warn">privileged</span>}
                  </div>
                  <p>{field.description}</p>
                </div>

                <label className="computer-toggle-row">
                  <span className="meta-label">Allow capability</span>
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(event) => update(field.key, event.target.checked)}
                  />
                </label>

                <div className="computer-tool-list">
                  {field.tools.map((tool) => (
                    <span key={tool} className="computer-tool-chip mono">
                      {tool}
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
