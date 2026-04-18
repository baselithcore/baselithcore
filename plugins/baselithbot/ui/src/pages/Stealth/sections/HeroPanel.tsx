import { Panel } from '../../../components/Panel';
import type { StealthConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { activeUserAgent } from '../helpers';

type HeroPanelProps = {
  draftConfig: StealthConfig;
  isDirty: boolean;
  normalizedLanguages: string[];
  normalizedUserAgents: string[];
  contextPreview: Array<{ label: string; value: string }>;
  headers: Array<{ label: string; value: string }>;
  update: <K extends keyof StealthConfig>(key: K, value: StealthConfig[K]) => void;
};

export function HeroPanel({
  draftConfig,
  isDirty,
  normalizedLanguages,
  normalizedUserAgents,
  contextPreview,
  headers,
  update,
}: HeroPanelProps) {
  return (
    <Panel className="stealth-hero-panel">
      <div className="stealth-hero">
        <div className="stealth-hero-copy">
          <span className="badge muted">runtime overlay</span>
          <h2>Session fingerprint policy</h2>
          <p>
            This tab controls how Baselithbot shapes each new Playwright BrowserContext: identity
            selection, locale/timezone signals, webdriver masking, and the extra stealth layer
            applied through init scripts and `playwright-stealth`.
          </p>

          <div className="chip-row">
            <span className={`badge ${draftConfig.enabled ? 'ok' : 'err'}`}>
              {draftConfig.enabled ? 'stealth active' : 'stealth bypassed'}
            </span>
            <span className={`badge ${draftConfig.rotate_user_agent ? 'warn' : 'muted'}`}>
              {draftConfig.rotate_user_agent ? 'rotating identity' : 'stable identity'}
            </span>
            <span className={`badge ${draftConfig.mask_webdriver ? 'ok' : 'warn'}`}>
              {draftConfig.mask_webdriver ? 'webdriver masked' : 'webdriver exposed'}
            </span>
            <span className={`badge ${isDirty ? 'warn' : 'muted'}`}>
              {isDirty ? 'unsaved changes' : 'saved'}
            </span>
          </div>

          <div className="stealth-hero-metrics">
            <div className="stealth-hero-metric">
              <span className="meta-label">User-Agent strategy</span>
              <strong>{draftConfig.rotate_user_agent ? 'Rotating' : 'Deterministic'}</strong>
              <span className="muted">{activeUserAgent(draftConfig)}</span>
            </div>
            <div className="stealth-hero-metric">
              <span className="meta-label">Locale profile</span>
              <strong>{normalizedLanguages[0] || 'Unset'}</strong>
              <span className="muted">
                {normalizedLanguages.length > 0
                  ? `${normalizedLanguages.join(', ')}`
                  : 'No navigator.language override'}
              </span>
            </div>
            <div className="stealth-hero-metric">
              <span className="meta-label">Timezone</span>
              <strong>{draftConfig.spoof_timezone.trim() || 'Unset'}</strong>
              <span className="muted">
                {draftConfig.enabled
                  ? 'Applied via Playwright timezone_id'
                  : 'Ignored while stealth is disabled'}
              </span>
            </div>
          </div>
        </div>

        <div className="stealth-switch-card">
          <div className="stealth-switch-head">
            <span className="meta-label">Stealth master</span>
            <span className={`badge ${draftConfig.enabled ? 'ok' : 'err'}`}>
              {draftConfig.enabled ? 'enabled' : 'disabled'}
            </span>
          </div>

          <label className="stealth-switch">
            <input
              type="checkbox"
              checked={draftConfig.enabled}
              onChange={(event) => update('enabled', event.target.checked)}
            />
            <span className="stealth-switch-copy">
              <strong>
                {draftConfig.enabled ? 'Stealth protections armed' : 'Default browser path'}
              </strong>
              <span>
                When enabled, Baselithbot applies BrowserContext options, extra headers, init
                scripts, and the optional `playwright-stealth` layer before interacting with the
                page.
              </span>
            </span>
          </label>

          <div className="stealth-switch-meta">
            <div className="stealth-kv">
              <span>Agent rebuild</span>
              <span>Required after save</span>
            </div>
            <div className="stealth-kv">
              <span>Context directives</span>
              <span>{formatNumber(contextPreview.length + headers.length)}</span>
            </div>
            <div className="stealth-kv">
              <span>User-Agent pool</span>
              <span>{formatNumber(normalizedUserAgents.length)} configured</span>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}
