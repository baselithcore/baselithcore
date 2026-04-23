import { Panel } from '../../../components/Panel';
import type { StealthConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';

type AppliedLayersProps = {
  draftConfig: StealthConfig;
  contextPreview: Array<{ label: string; value: string }>;
  headers: Array<{ label: string; value: string }>;
};

export function AppliedLayers({ draftConfig, contextPreview, headers }: AppliedLayersProps) {
  return (
    <Panel title="Applied layers" tag={`${formatNumber(contextPreview.length)} context opts`}>
      <div className="stack-section">
        <div>
          <div className="section-label">BrowserContext options</div>
          {contextPreview.length === 0 ? (
            <div className="info-block">No BrowserContext spoofing options will be applied.</div>
          ) : (
            <div className="stealth-surface-list">
              {contextPreview.map((entry) => (
                <div key={entry.label} className="stealth-surface-item">
                  <span className="mono">{entry.label}</span>
                  <span>{entry.value}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <div className="section-label">Header + JS mutations</div>
          <div className="stealth-chip-list">
            <span className="stealth-chip">
              {headers.length > 0 ? `Accept-Language: ${headers[0].value}` : 'No header override'}
            </span>
            <span className="stealth-chip">
              {draftConfig.mask_webdriver
                ? 'navigator.webdriver hidden'
                : 'navigator.webdriver untouched'}
            </span>
            <span className="stealth-chip">WebGL vendor script mutation</span>
            <span className="stealth-chip">navigator.plugins synthetic stub</span>
          </div>
        </div>
      </div>
    </Panel>
  );
}
