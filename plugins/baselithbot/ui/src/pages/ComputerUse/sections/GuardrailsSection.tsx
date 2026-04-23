import { Panel } from '../../../components/Panel';
import { type ComputerUseConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';

type GuardrailsSectionProps = {
  draftConfig: ComputerUseConfig;
  allowlistDraft: string;
  setAllowlistDraft: (value: string) => void;
  normalizedAllowlist: string[];
  shellConfigured: boolean;
  update: <K extends keyof ComputerUseConfig>(key: K, value: ComputerUseConfig[K]) => void;
};

export function GuardrailsSection({
  draftConfig,
  allowlistDraft,
  setAllowlistDraft,
  normalizedAllowlist,
  shellConfigured,
  update,
}: GuardrailsSectionProps) {
  return (
    <Panel title="Execution guardrails" tag="shell + filesystem">
      <div className="computer-guard-grid">
        <div className="computer-allowlist-column">
          <div className="computer-field-head">
            <div>
              <div className="section-label">Shell allowlist</div>
              <div className="computer-field-copy">
                One entry per line. First-token exact match or prefix match. Empty means shell
                execution stays blocked even when the shell capability is on.
              </div>
            </div>
            <span className={`badge ${shellConfigured ? 'ok' : 'warn'}`}>
              {formatNumber(normalizedAllowlist.length)} entries
            </span>
          </div>

          <textarea
            className="textarea computer-allowlist"
            value={allowlistDraft}
            onChange={(event) => setAllowlistDraft(event.target.value)}
            placeholder={'ls\npwd\ngit status\npython -m pytest'}
          />
        </div>

        <div className="computer-settings-column">
          <label className="form-row">
            <span>Shell timeout (seconds)</span>
            <input
              type="number"
              min={1}
              max={600}
              step={1}
              className="input"
              value={draftConfig.shell_timeout_seconds}
              onChange={(event) => update('shell_timeout_seconds', Number(event.target.value))}
            />
          </label>

          <label className="form-row">
            <span>Filesystem root</span>
            <input
              type="text"
              className="input mono"
              placeholder="/tmp/baselithbot-sandbox"
              value={draftConfig.filesystem_root ?? ''}
              onChange={(event) => update('filesystem_root', event.target.value || null)}
            />
          </label>

          <label className="form-row">
            <span>Filesystem max bytes / write</span>
            <input
              type="number"
              min={1}
              step={1}
              className="input"
              value={draftConfig.filesystem_max_bytes}
              onChange={(event) => update('filesystem_max_bytes', Number(event.target.value))}
            />
          </label>

          <label className="form-row">
            <span>Audit log path (JSONL)</span>
            <input
              type="text"
              className="input mono"
              placeholder="/tmp/baselithbot-sandbox/audit.jsonl"
              value={draftConfig.audit_log_path ?? ''}
              onChange={(event) => update('audit_log_path', event.target.value || null)}
            />
          </label>
        </div>
      </div>
    </Panel>
  );
}
