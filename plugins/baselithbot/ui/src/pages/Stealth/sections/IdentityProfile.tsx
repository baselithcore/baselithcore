import { Panel } from '../../../components/Panel';
import type { StealthConfig } from '../../../lib/api';

type IdentityProfileProps = {
  draftConfig: StealthConfig;
  langDraft: string;
  setLangDraft: (value: string) => void;
  normalizedLanguages: string[];
  headers: Array<{ label: string; value: string }>;
  update: <K extends keyof StealthConfig>(key: K, value: StealthConfig[K]) => void;
};

export function IdentityProfile({
  draftConfig,
  langDraft,
  setLangDraft,
  normalizedLanguages,
  headers,
  update,
}: IdentityProfileProps) {
  return (
    <Panel title="Identity profile" tag="locale + timezone">
      <div className="stealth-form-grid">
        <label className="form-row">
          <span>Languages</span>
          <input
            type="text"
            className="input mono"
            value={langDraft}
            onChange={(event) => setLangDraft(event.target.value)}
            placeholder="en-US, en"
          />
        </label>

        <label className="form-row">
          <span>Timezone</span>
          <input
            type="text"
            className="input mono"
            value={draftConfig.spoof_timezone}
            onChange={(event) => update('spoof_timezone', event.target.value)}
            placeholder="UTC"
          />
        </label>
      </div>

      <div className="stealth-preview-grid">
        <div className="meta-tile">
          <span className="meta-label">Accept-Language</span>
          <span>{headers[0]?.value || 'Not applied'}</span>
        </div>
        <div className="meta-tile">
          <span className="meta-label">navigator.language</span>
          <span>{normalizedLanguages[0] || 'Not overridden'}</span>
        </div>
        <div className="meta-tile">
          <span className="meta-label">navigator.languages</span>
          <span>{normalizedLanguages.length > 0 ? normalizedLanguages.join(', ') : 'None'}</span>
        </div>
        <div className="meta-tile">
          <span className="meta-label">timezone_id</span>
          <span>{draftConfig.spoof_timezone.trim() || 'Not applied'}</span>
        </div>
      </div>
    </Panel>
  );
}
