import type { AgentInfo } from '../../../lib/api';
import { Panel } from '../../../components/Panel';
import { truncate } from '../../../lib/format';

interface AgentRosterProps {
  agents: AgentInfo[];
  active: AgentInfo | null;
  onSelect: (name: string) => void;
}

export function AgentRoster({ agents, active, onSelect }: AgentRosterProps) {
  return (
    <Panel title="Roster" tag={`${agents.length}`}>
      <div className="stack-list">
        {agents.map((agent) => {
          const isActive = agent.name === active?.name;
          return (
            <button
              key={agent.name}
              type="button"
              className={`select-row ${isActive ? 'active' : ''}`}
              onClick={() => onSelect(agent.name)}
            >
              <div className="select-row-head">
                <span className="mono" style={{ color: 'var(--ink-100)' }}>
                  {agent.name}
                </span>
                <span className={`badge ${agent.custom ? 'ok' : 'muted'}`}>
                  {agent.custom ? 'custom' : 'system'}
                </span>
                <span className="badge">p{agent.priority}</span>
              </div>
              <div className="muted" style={{ fontSize: 12 }}>
                {truncate(agent.description || 'No description provided.', 110)}
              </div>
            </button>
          );
        })}
      </div>
    </Panel>
  );
}
