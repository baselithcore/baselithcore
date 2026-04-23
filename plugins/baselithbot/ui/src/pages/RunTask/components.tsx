import { Icon, paths } from '../../lib/icons';
import { detectLink } from './helpers';

export function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-tile">
      <span className="meta-label">{label}</span>
      <span className="mono" style={{ color: 'var(--ink-100)' }}>
        {value}
      </span>
    </div>
  );
}

function ExtractedScalar({ value }: { value: string }) {
  const link = detectLink(value);
  if (!link) {
    return <span className="extracted-scalar">{value}</span>;
  }
  const isExternal = link.href.startsWith('http');
  return (
    <a
      className="extracted-link"
      href={link.href}
      target={isExternal ? '_blank' : undefined}
      rel={isExternal ? 'noopener noreferrer' : undefined}
      aria-label={isExternal ? `${link.label} (opens in new tab)` : link.label}
      title={link.href}
    >
      <span className="extracted-link-label">{link.label}</span>
      {isExternal ? (
        <Icon path={paths.externalLink} className="extracted-link-icon" size={12} aria-hidden />
      ) : null}
    </a>
  );
}

export function ExtractedValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="extracted-empty">—</span>;
  }
  if (Array.isArray(value)) {
    return (
      <ol className="extracted-list">
        {value.map((item, idx) => (
          <li key={idx}>
            <ExtractedValue value={item} />
          </li>
        ))}
      </ol>
    );
  }
  if (typeof value === 'object') {
    return (
      <dl className="extracted-object">
        {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <div key={k} className="extracted-object-row">
            <dt>{k}</dt>
            <dd>
              <ExtractedValue value={v} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <ExtractedScalar value={String(value)} />;
}

export function ExtractedDataView({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="extracted-data">
      {Object.entries(data).map(([field, value]) => (
        <div key={field} className="extracted-field">
          <div className="extracted-field-header">
            <span className="extracted-field-name">{field}</span>
            <span className="extracted-field-count">
              {Array.isArray(value)
                ? `${value.length} item${value.length === 1 ? '' : 's'}`
                : typeof value}
            </span>
          </div>
          <ExtractedValue value={value} />
        </div>
      ))}
    </div>
  );
}
