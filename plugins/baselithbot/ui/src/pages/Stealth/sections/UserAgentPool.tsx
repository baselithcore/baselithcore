import { Panel } from '../../../components/Panel';
import type { StealthConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { activeUserAgent } from '../helpers';

type UserAgentPoolProps = {
  draftConfig: StealthConfig;
  uaDraft: string;
  setUaDraft: (value: string) => void;
  normalizedUserAgents: string[];
};

export function UserAgentPool({
  draftConfig,
  uaDraft,
  setUaDraft,
  normalizedUserAgents,
}: UserAgentPoolProps) {
  return (
    <Panel title="User-Agent pool" tag={`${formatNumber(normalizedUserAgents.length)} entries`}>
      <div className="stealth-ua-panel">
        <div className="stealth-ua-copy">
          <div className="section-label">Selection strategy</div>
          <div className="stealth-ua-title">
            {normalizedUserAgents.length === 0
              ? 'Browser default user agent'
              : draftConfig.rotate_user_agent
                ? 'Randomized per session'
                : 'Pinned to first entry'}
          </div>
          <div className="stealth-field-copy">
            When stealth is enabled, the selected user agent is now applied directly to the
            Playwright BrowserContext, not just to request headers.
          </div>
          <div className="info-block">
            Active profile: <span className="mono">{activeUserAgent(draftConfig)}</span>
          </div>
        </div>

        <textarea
          className="textarea stealth-ua-textarea"
          value={uaDraft}
          onChange={(event) => setUaDraft(event.target.value)}
          placeholder={
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36\nMozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
          }
        />
      </div>
    </Panel>
  );
}
