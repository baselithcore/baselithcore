import { Panel } from '../../../components/Panel';
import { formatNumber } from '../../../lib/format';
import { capabilityTone, type CapabilitySpec } from '../helpers';

type ToolSurfaceSectionProps = {
  enabledCapabilities: CapabilitySpec[];
  enabledToolCount: number;
};

export function ToolSurfaceSection({
  enabledCapabilities,
  enabledToolCount,
}: ToolSurfaceSectionProps) {
  return (
    <Panel title="Integrated tool surface" tag={`${formatNumber(enabledToolCount)} exposed`}>
      {enabledCapabilities.length === 0 ? (
        <div className="info-block">
          No Computer Use capability is currently enabled, so Baselithbot will not expose any
          desktop, shell, or filesystem MCP entry point.
        </div>
      ) : (
        <div className="computer-integrated-grid">
          {enabledCapabilities.map((field) => (
            <section key={field.key} className="computer-integrated-card">
              <div className="computer-integrated-head">
                <div>
                  <div className="section-label">{field.label}</div>
                  <strong>{formatNumber(field.tools.length)} tool(s)</strong>
                </div>
                <span className={`badge ${capabilityTone(field.accent)}`}>live</span>
              </div>
              <div className="computer-tool-list">
                {field.tools.map((tool) => (
                  <span key={tool} className="computer-tool-chip mono">
                    {tool}
                  </span>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </Panel>
  );
}
