import { DetailDrawer } from '../../../components/DetailDrawer';
import type { Skill } from '../../../lib/api';
import { Icon, paths } from '../../../lib/icons';
import { MetaTile } from '../components';
import { scopeLabel, type ScopeName } from '../helpers';

interface SkillDetailProps {
  selected: Skill | null;
  onClose: () => void;
  onRequestRemove: (skill: Skill) => void;
  removePending: boolean;
}

export function SkillDetail({
  selected,
  onClose,
  onRequestRemove,
  removePending,
}: SkillDetailProps) {
  return (
    <DetailDrawer
      open={!!selected}
      title={selected?.name ?? ''}
      subtitle={
        selected ? `${scopeLabel(selected.scope as ScopeName)} skill details` : 'Skill details'
      }
      onClose={onClose}
    >
      {selected && (
        <>
          <div className="detail-grid">
            <MetaTile label="Scope" value={selected.scope} />
            <MetaTile label="Version" value={selected.version} />
            <MetaTile label="Entrypoint" value={selected.entrypoint || '—'} />
          </div>

          <div className="stack-section">
            <div className="section-label">Description</div>
            <div className="info-block">
              {selected.description || 'No description provided for this skill entry.'}
            </div>
          </div>

          <div className="stack-section">
            <div className="section-label">Metadata</div>
            <pre className="code-block">{JSON.stringify(selected.metadata ?? {}, null, 2)}</pre>
          </div>

          {selected.scope !== 'bundled' && (
            <div className="stack-section">
              <button
                type="button"
                className="btn danger"
                disabled={removePending}
                onClick={() => onRequestRemove(selected)}
              >
                <Icon path={paths.trash} size={12} />
                {removePending ? 'Removing…' : 'Remove skill'}
              </button>
            </div>
          )}
        </>
      )}
    </DetailDrawer>
  );
}
