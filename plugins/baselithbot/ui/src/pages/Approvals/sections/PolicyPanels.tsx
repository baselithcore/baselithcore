import { Panel } from '../../../components/Panel';
import type { ApprovalPolicy } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { formatCapability, formatDuration } from '../helpers';

interface PolicyPanelsProps {
  policy: ApprovalPolicy;
  topCapabilities: Array<[string, number]>;
  topActions: Array<[string, number]>;
}

export function PolicyPanels({ policy, topCapabilities, topActions }: PolicyPanelsProps) {
  return (
    <section className="grid grid-split-2-1">
      <Panel title="Gate coverage" tag="computer use policy">
        <div className="detail-grid">
          <div className="meta-tile">
            <span className="meta-label">Enabled surfaces</span>
            <span>{formatNumber(policy.enabled_capabilities.length)}</span>
            <span className="muted">
              {policy.enabled_capabilities.length > 0
                ? policy.enabled_capabilities.map(formatCapability).join(', ')
                : 'Computer Use is effectively off'}
            </span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Gated surfaces</span>
            <span>{formatNumber(policy.gated_capabilities.length)}</span>
            <span className="muted">
              {policy.gated_capabilities.length > 0
                ? policy.gated_capabilities.map(formatCapability).join(', ')
                : 'No enabled capability requires approval'}
            </span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Bypassed surfaces</span>
            <span>{formatNumber(policy.bypassed_capabilities.length)}</span>
            <span className="muted">
              {policy.bypassed_capabilities.length > 0
                ? policy.bypassed_capabilities.map(formatCapability).join(', ')
                : 'All enabled capabilities are gated'}
            </span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Timeout policy</span>
            <span>{formatDuration(policy.approval_timeout_seconds)}</span>
            <span className="muted">Applies to every live approval request</span>
          </div>
        </div>

        <div className="approvals-policy-grid">
          <div className="approvals-policy-card">
            <span className="section-label">Gated capabilities</span>
            {policy.gated_capabilities.length === 0 ? (
              <div className="info-block">
                No enabled Computer Use capability currently routes through the operator gate.
              </div>
            ) : (
              <div className="chip-row">
                {policy.gated_capabilities.map((capability) => (
                  <span key={capability} className="badge ok">
                    {formatCapability(capability)}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="approvals-policy-card">
            <span className="section-label">Direct execution surfaces</span>
            {policy.bypassed_capabilities.length === 0 ? (
              <div className="info-block">No enabled surface bypasses the operator gate.</div>
            ) : (
              <div className="chip-row">
                {policy.bypassed_capabilities.map((capability) => (
                  <span key={capability} className="badge muted">
                    {formatCapability(capability)}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </Panel>

      <Panel title="Hot paths" tag="visible requests">
        <div className="approvals-summary-block">
          <span className="section-label">Capabilities</span>
          {topCapabilities.length === 0 ? (
            <div className="info-block">No visible request mix yet.</div>
          ) : (
            <div className="approvals-summary-list">
              {topCapabilities.map(([capability, count]) => (
                <div key={capability} className="approvals-kv">
                  <span>{formatCapability(capability)}</span>
                  <span>{formatNumber(count)}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="approvals-summary-block">
          <span className="section-label">Actions</span>
          {topActions.length === 0 ? (
            <div className="info-block">No action summary available yet.</div>
          ) : (
            <div className="approvals-summary-list">
              {topActions.map(([action, count]) => (
                <div key={action} className="approvals-kv">
                  <span className="mono">{action}</span>
                  <span>{formatNumber(count)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Panel>
    </section>
  );
}
