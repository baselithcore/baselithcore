import { Panel } from '../../../components/Panel';
import type { DesktopToolPolicy, DesktopToolSpec } from '../../../lib/api';
import { Icon, paths } from '../../../lib/icons';
import { CAPABILITIES, type CapabilitySpec } from '../constants';
import { capabilityForTool, requiredFields } from '../helpers';

interface HeroCatalogProps {
  policy: DesktopToolPolicy;
  tools: DesktopToolSpec[];
  toolNames: Set<string>;
  enabledCapabilities: CapabilitySpec[];
  gatedCapabilities: CapabilitySpec[];
  missingExpectedTools: string[];
  ready: boolean;
  policyMessages: string[];
}

export function HeroCatalogSection({
  policy,
  tools,
  toolNames,
  missingExpectedTools,
  ready,
  policyMessages,
}: HeroCatalogProps) {
  return (
    <section className="grid grid-split-2-1">
      <Panel className="desktop-hero-panel">
        <div className="desktop-hero">
          <div className="desktop-hero-copy">
            <div className="desktop-hero-badges">
              <span className={`pill ${policy.enabled ? 'ok' : 'down'}`}>
                <span className="dot" />
                {policy.enabled ? 'Desktop runtime armed' : 'Desktop runtime locked'}
              </span>
              <span className={`pill ${ready ? 'ok' : 'warn'}`}>
                <span className="dot" />
                {ready ? 'Catalog ready' : 'Operator attention needed'}
              </span>
            </div>
            <h2>Runtime policy and plugin export are now surfaced together.</h2>
            <p className="muted">
              This tab no longer assumes a fixed tool surface. It reads the live catalog exported
              by the plugin, shows which capabilities are enabled, and highlights policy gaps
              before dispatching any host action.
            </p>
          </div>

          <div className="desktop-capability-grid">
            {CAPABILITIES.map((capability) => {
              const availableCount = capability.toolNames.filter((toolName) =>
                toolNames.has(toolName)
              ).length;
              const approvalRequired = policy.require_approval_for.includes(capability.approvalKey);
              return (
                <div
                  key={capability.key}
                  className={`desktop-capability-card ${policy[capability.key] ? 'enabled' : 'disabled'}`}
                >
                  <div className="desktop-capability-head">
                    <div className="desktop-capability-icon">
                      <Icon path={paths[capability.icon]} size={16} />
                    </div>
                    <div>
                      <strong>{capability.label}</strong>
                      <p>{capability.description}</p>
                    </div>
                  </div>
                  <div className="chip-row">
                    <span className={`badge ${policy[capability.key] ? 'ok' : 'muted'}`}>
                      {policy[capability.key] ? 'enabled' : 'disabled'}
                    </span>
                    <span className="badge muted">{availableCount} tool(s)</span>
                    {approvalRequired && <span className="badge warn">approval</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="desktop-meta-grid">
          <div className="desktop-meta-card">
            <span>Shell allowlist</span>
            <strong>{policy.allowed_shell_commands.length}</strong>
            <p>{policy.allowed_shell_commands.join(', ') || 'No commands allowlisted'}</p>
          </div>
          <div className="desktop-meta-card">
            <span>Filesystem root</span>
            <strong>{policy.filesystem_root ? 'configured' : 'missing'}</strong>
            <p>{policy.filesystem_root ?? 'No filesystem root configured'}</p>
          </div>
          <div className="desktop-meta-card">
            <span>Audit sink</span>
            <strong>{policy.audit_log_path ? 'persisted' : 'volatile'}</strong>
            <p>{policy.audit_log_path ?? 'No audit JSONL path configured'}</p>
          </div>
        </div>

        {!ready && (
          <div className="computer-warning-list" style={{ marginTop: 18 }}>
            {policyMessages.map((message) => (
              <div key={message} className="computer-warning-item">
                <Icon path={paths.shieldOff} size={14} />
                <span>{message}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Plugin catalog" tag={`${tools.length} exported`}>
        {missingExpectedTools.length > 0 && (
          <div className="computer-warning-list" style={{ marginBottom: 14 }}>
            {missingExpectedTools.map((toolName) => (
              <div key={toolName} className="computer-warning-item">
                <Icon path={paths.shieldOff} size={14} />
                <span>{toolName} is expected by the UI but not exported by the plugin.</span>
              </div>
            ))}
          </div>
        )}

        <div className="desktop-tool-grid">
          {tools.map((tool) => {
            const capability = capabilityForTool(tool.name);
            const enabled = capability ? policy[capability.key] : false;
            const gated = capability
              ? policy.require_approval_for.includes(capability.approvalKey)
              : false;
            return (
              <div key={tool.name} className="desktop-tool-card">
                <div className="desktop-tool-head">
                  <span className={`badge ${enabled ? 'ok' : 'muted'}`}>
                    {capability?.label ?? 'tool'}
                  </span>
                  {gated && <span className="badge warn">approval</span>}
                </div>
                <strong>{tool.name.replace(/^baselithbot_/, '')}</strong>
                <p>{tool.description}</p>
                <div className="desktop-tool-meta">
                  <span>{requiredFields(tool)}</span>
                  <span>{Object.keys(tool.input_schema.properties ?? {}).length} arg(s)</span>
                </div>
              </div>
            );
          })}
        </div>
      </Panel>
    </section>
  );
}
