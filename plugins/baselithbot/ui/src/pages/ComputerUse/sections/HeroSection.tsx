import { Panel } from '../../../components/Panel';
import { type ComputerUseConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { summariseAllowlist } from '../helpers';

type HeroSectionProps = {
  draftConfig: ComputerUseConfig;
  privilegedEnabled: boolean;
  auditConfigured: boolean;
  filesystemConfigured: boolean;
  isDirty: boolean;
  enabledToolCount: number;
  normalizedAllowlist: string[];
  update: <K extends keyof ComputerUseConfig>(key: K, value: ComputerUseConfig[K]) => void;
};

export function HeroSection({
  draftConfig,
  privilegedEnabled,
  auditConfigured,
  filesystemConfigured,
  isDirty,
  enabledToolCount,
  normalizedAllowlist,
  update,
}: HeroSectionProps) {
  return (
    <Panel className="computer-hero-panel">
      <div className="computer-hero">
        <div className="computer-hero-copy">
          <span className="badge muted">runtime overlay</span>
          <h2>Operator-level access policy</h2>
          <p>
            This surface controls which desktop, shell, and filesystem tools the plugin can expose.
            Every enabled capability expands the live tool surface for the next Baselithbot agent
            instance.
          </p>

          <div className="chip-row">
            <span className={`badge ${draftConfig.enabled ? 'ok' : 'err'}`}>
              {draftConfig.enabled ? 'armed' : 'locked'}
            </span>
            <span className={`badge ${privilegedEnabled ? 'warn' : 'muted'}`}>
              {privilegedEnabled ? 'privileged path open' : 'no privileged access'}
            </span>
            <span className={`badge ${auditConfigured ? 'ok' : 'warn'}`}>
              {auditConfigured ? 'audit persisted' : 'no audit sink'}
            </span>
            <span className={`badge ${isDirty ? 'warn' : 'muted'}`}>
              {isDirty ? 'unsaved changes' : 'saved'}
            </span>
          </div>

          <div className="computer-hero-metrics">
            <div className="computer-hero-metric">
              <span className="meta-label">Guarded tools</span>
              <strong>{formatNumber(enabledToolCount)}</strong>
              <span className="muted">currently exposed by this policy</span>
            </div>
            <div className="computer-hero-metric">
              <span className="meta-label">Shell allowlist</span>
              <strong>{formatNumber(normalizedAllowlist.length)}</strong>
              <span className="muted">{summariseAllowlist(normalizedAllowlist)}</span>
            </div>
            <div className="computer-hero-metric">
              <span className="meta-label">Filesystem boundary</span>
              <strong>{filesystemConfigured ? 'Scoped' : 'Unset'}</strong>
              <span className="muted">
                {draftConfig.filesystem_root?.trim() || 'Configure a root to allow file ops'}
              </span>
            </div>
          </div>
        </div>

        <div className="computer-switch-card">
          <div className="computer-switch-head">
            <span className="meta-label">Master switch</span>
            <span className={`badge ${draftConfig.enabled ? 'ok' : 'err'}`}>
              {draftConfig.enabled ? 'enabled' : 'disabled'}
            </span>
          </div>

          <label className="computer-switch">
            <input
              type="checkbox"
              checked={draftConfig.enabled}
              onChange={(event) => update('enabled', event.target.checked)}
            />
            <span className="computer-switch-copy">
              <strong>{draftConfig.enabled ? 'Computer Use armed' : 'Computer Use locked'}</strong>
              <span>
                When disabled, every Computer Use entry point returns a denied result without
                touching the OS.
              </span>
            </span>
          </label>

          <div className="computer-switch-meta">
            <div className="computer-kv">
              <span>Agent rebuild</span>
              <span>Required after save</span>
            </div>
            <div className="computer-kv">
              <span>Audit log</span>
              <span>{draftConfig.audit_log_path?.trim() || 'Not configured'}</span>
            </div>
            <div className="computer-kv">
              <span>Write ceiling</span>
              <span>{formatNumber(draftConfig.filesystem_max_bytes)} bytes</span>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}
