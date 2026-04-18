import { Panel } from '../../components/Panel';

export function MetaPanel({ title, value }: { title: string; value: string }) {
  return (
    <Panel title={title}>
      <div className="info-block mono" style={{ color: 'var(--ink-100)' }}>
        {value}
      </div>
    </Panel>
  );
}
