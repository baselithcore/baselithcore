import { Panel } from '../../../components/Panel';
import type { StealthConfig } from '../../../lib/api';
import { Icon, paths } from '../../../lib/icons';

type CoverageReviewProps = {
  draftConfig: StealthConfig;
  normalizedLanguages: string[];
  warnings: string[];
};

export function CoverageReview({
  draftConfig,
  normalizedLanguages,
  warnings,
}: CoverageReviewProps) {
  return (
    <Panel title="Coverage review" tag="before save">
      <div className="stack-section">
        <div className="detail-grid">
          <div className="meta-tile">
            <span className="meta-label">Master</span>
            <span>{draftConfig.enabled ? 'On' : 'Off'}</span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Rotation</span>
            <span>{draftConfig.rotate_user_agent ? 'Randomized' : 'Fixed'}</span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Languages</span>
            <span>{normalizedLanguages.length > 0 ? normalizedLanguages[0] : 'None'}</span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Timezone</span>
            <span>{draftConfig.spoof_timezone.trim() || 'None'}</span>
          </div>
        </div>

        {warnings.length === 0 ? (
          <div className="info-block">
            Stealth policy is coherent: the active toggles have enough data to produce a consistent
            BrowserContext fingerprint profile.
          </div>
        ) : (
          <div className="stealth-warning-list">
            {warnings.map((warning) => (
              <div key={warning} className="stealth-warning-item">
                <Icon path={paths.shieldOff} size={14} />
                <span>{warning}</span>
              </div>
            ))}
          </div>
        )}

        <div className="stealth-policy-note">
          `playwright-stealth` remains additive: this dashboard config now applies the core
          BrowserContext signals directly, even before the external stealth package runs.
        </div>
      </div>
    </Panel>
  );
}
