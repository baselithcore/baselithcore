import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import { Skeleton } from '../../../components/Skeleton';
import type { WorkspaceSkillReport } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { toErrorMessage, validationTone, workspaceReportSummary } from '../helpers';

interface ValidationPanelProps {
  isLoading: boolean;
  error: unknown;
  data:
    | {
        reports: WorkspaceSkillReport[];
        counts: { verified: number; provisional: number; invalid: number };
      }
    | undefined;
}

export function ValidationPanel({ isLoading, error, data }: ValidationPanelProps) {
  return (
    <Panel
      title="Local custom skill validation"
      tag={data ? `${formatNumber(data.counts.verified)} verified` : 'workspace'}
    >
      {isLoading ? (
        <Skeleton height={180} />
      ) : error ? (
        <div className="info-block">Validation unavailable: {toErrorMessage(error)}</div>
      ) : !data || data.reports.length === 0 ? (
        <EmptyState
          title="No local workspace bundles found"
          description="Create `skills/<name>/SKILL.md` for custom local skills, or keep using AGENTS/SOUL/TOOLS prompt bundles."
        />
      ) : (
        <div className="stack-section">
          <div className="skills-chip-row">
            <span className="badge ok">{formatNumber(data.counts.verified)} verified</span>
            <span className="badge warn">{formatNumber(data.counts.provisional)} provisional</span>
            <span className="badge err">{formatNumber(data.counts.invalid)} invalid</span>
          </div>

          <div className="cards-grid skills-grid">
            {data.reports.map((report) => (
              <div key={`${report.kind}:${report.entrypoint}`} className="record-card skill-card">
                <div className="skills-card-head">
                  <div className="skills-card-heading">
                    <div className="record-card-title mono">{report.name}</div>
                    <div className="skills-card-summary">{workspaceReportSummary(report)}</div>
                  </div>
                  <span className={`badge ${validationTone(report.validation.status)}`}>
                    {report.validation.status}
                  </span>
                </div>

                <div className="skills-card-entry mono" title={report.entrypoint}>
                  {report.entrypoint}
                </div>

                <div className="skills-card-meta">
                  <span className="badge muted">{report.kind}</span>
                  {report.validation.surfaces.map((surface) => (
                    <span key={surface} className="badge muted">
                      {surface}
                    </span>
                  ))}
                </div>

                {report.validation.errors.length > 0 && (
                  <div className="info-block" style={{ color: 'var(--accent-rose)' }}>
                    {report.validation.errors.join(' ')}
                  </div>
                )}

                {report.validation.warnings.length > 0 && (
                  <div className="info-block">{report.validation.warnings.join(' ')}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}
