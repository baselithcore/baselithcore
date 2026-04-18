import { Panel } from '../../../components/Panel';
import { type ComputerUseConfig } from '../../../lib/api';
import { Icon, paths } from '../../../lib/icons';

type RiskReviewSectionProps = {
  draftConfig: ComputerUseConfig;
  auditConfigured: boolean;
  warnings: string[];
};

export function RiskReviewSection({
  draftConfig,
  auditConfigured,
  warnings,
}: RiskReviewSectionProps) {
  return (
    <Panel title="Risk review" tag="before save">
      <div className="stack-section">
        <div className="detail-grid">
          <div className="meta-tile">
            <span className="meta-label">Shell</span>
            <span>{draftConfig.allow_shell ? 'Enabled' : 'Disabled'}</span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Filesystem</span>
            <span>{draftConfig.allow_filesystem ? 'Enabled' : 'Disabled'}</span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Timeout</span>
            <span>{draftConfig.shell_timeout_seconds}s</span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Audit trail</span>
            <span>{auditConfigured ? 'Persisted' : 'Not persisted'}</span>
          </div>
        </div>

        {warnings.length === 0 ? (
          <div className="info-block">
            Current policy is internally coherent: enabled capabilities have the supporting
            guardrails they need.
          </div>
        ) : (
          <div className="computer-warning-list">
            {warnings.map((warning) => (
              <div key={warning} className="computer-warning-item">
                <Icon path={paths.shieldOff} size={14} />
                <span>{warning}</span>
              </div>
            ))}
          </div>
        )}

        <div className="computer-policy-note">
          Privileged capabilities are intentionally split from observation capabilities. Keep
          screenshots, mouse, and keyboard separate from shell/filesystem unless the task genuinely
          needs host mutation.
        </div>
      </div>
    </Panel>
  );
}
