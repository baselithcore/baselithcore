import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import { Skeleton } from '../../../components/Skeleton';
import { formatNumber } from '../../../lib/format';
import { Icon, paths } from '../../../lib/icons';
import { toErrorMessage, type CatalogSkill } from '../helpers';

interface ClawhubCatalogProps {
  error: unknown;
  isLoading: boolean;
  installableCatalog: CatalogSkill[];
  catalogPreview: CatalogSkill[];
  catalogOverflow: number;
  installingName: string | null;
  installPending: boolean;
  onInstall: (name: string) => void;
}

export function ClawhubCatalog({
  error,
  isLoading,
  installableCatalog,
  catalogPreview,
  catalogOverflow,
  installingName,
  installPending,
  onInstall,
}: ClawhubCatalogProps) {
  return (
    <Panel
      title="Installable from ClawHub"
      tag={`${formatNumber(installableCatalog.length)} available`}
    >
      {error ? (
        <div className="info-block">Catalog unavailable: {toErrorMessage(error)}</div>
      ) : isLoading ? (
        <Skeleton height={200} />
      ) : installableCatalog.length === 0 ? (
        <EmptyState
          title="No installable catalog entries"
          description="Either the registry is empty or every catalog skill is already installed locally."
        />
      ) : (
        <div className="stack-section">
          {catalogOverflow > 0 && (
            <div className="info-block">
              Showing the first {catalogPreview.length} installable entries. Use quick install if
              you need a specific package by name.
            </div>
          )}

          <div className="cards-grid skills-grid">
            {catalogPreview.map((entry) => (
              <div key={entry.name} className="record-card skill-card catalog-skill-card">
                <div className="skills-card-head">
                  <div className="skills-card-heading">
                    <div className="record-card-title mono">{entry.name}</div>
                    <div className="skills-card-summary">
                      Available from the remote ClawHub registry
                    </div>
                  </div>
                  <span className="badge ok">catalog</span>
                </div>

                <div className="skills-card-description">
                  {entry.description || 'No description provided for this catalog entry.'}
                </div>

                <div className="skills-card-meta">
                  <span className="badge muted mono">v{entry.version}</span>
                  {entry.entrypoint && <span className="badge muted">entrypoint declared</span>}
                </div>

                <div className="skills-card-entry mono" title={entry.entrypoint ?? undefined}>
                  {entry.entrypoint || 'Remote package artifact'}
                </div>

                <div className="skills-card-actions">
                  <button
                    type="button"
                    className="btn primary sm"
                    disabled={installPending}
                    onClick={() => onInstall(entry.name)}
                  >
                    <Icon path={paths.plus} size={12} />
                    {installingName === entry.name ? 'Installing…' : 'Install'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}
