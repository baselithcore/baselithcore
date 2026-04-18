import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import type { DesktopToolInvocation, DesktopToolSpec } from '../../../lib/api';
import { formatRelative } from '../../../lib/format';
import { Icon, paths } from '../../../lib/icons';
import {
  compactArgs,
  requiredFields,
  resultMimeType,
  screenshotless,
  statusTone,
  summarizeResult,
  type RunLogEntry,
} from '../helpers';

interface InspectorHistoryProps {
  runLog: RunLogEntry[];
  selectedEntry: RunLogEntry | null;
  selectedTool: DesktopToolSpec | undefined;
  screenshotBase64: string | undefined;
  onSelect: (id: string) => void;
}

export function InspectorHistorySection({
  runLog,
  selectedEntry,
  selectedTool,
  screenshotBase64,
  onSelect,
}: InspectorHistoryProps) {
  return (
    <div className="desktop-stack">
      <Panel title="Invocation inspector" tag={selectedEntry ? selectedEntry.tool : 'idle'}>
        {!selectedEntry ? (
          <EmptyState
            title="No invocations yet"
            description="Dispatch a desktop tool to inspect payloads, screenshots, stdout, and filesystem results."
          />
        ) : (
          <div className="desktop-result-stack">
            <div className="desktop-result-head">
              <div>
                <div className="inline">
                  <span className={`badge ${statusTone(selectedEntry.result.status)}`}>
                    {selectedEntry.result.status}
                  </span>
                  <span className="badge muted mono">{selectedEntry.tool}</span>
                  {selectedTool && (
                    <span className="badge">{requiredFields(selectedTool)}</span>
                  )}
                </div>
                <p className="muted" style={{ margin: '10px 0 0', fontSize: 13 }}>
                  {selectedTool?.description ?? 'Tool metadata unavailable'} ·{' '}
                  {formatRelative(selectedEntry.ts / 1000)}
                </p>
              </div>
              <button
                type="button"
                className="btn ghost"
                onClick={() => onSelect(selectedEntry.id)}
              >
                <Icon path={paths.externalLink} size={14} />
                Focused
              </button>
            </div>

            {screenshotBase64 && (
              <img
                className="screenshot"
                alt="Desktop screenshot"
                src={`data:${resultMimeType(selectedEntry.result)};base64,${screenshotBase64}`}
              />
            )}

            <div className="desktop-code-block">
              <div className="desktop-code-header">args</div>
              <pre className="mono">{JSON.stringify(selectedEntry.args, null, 2)}</pre>
            </div>

            <ResultBlocks result={selectedEntry.result} />
          </div>
        )}
      </Panel>

      <Panel title="Recent invocations" tag={String(runLog.length)}>
        {runLog.length === 0 ? (
          <EmptyState
            title="No history"
            description="The latest 20 desktop calls will appear here."
          />
        ) : (
          <div className="stack-list">
            {runLog.map((entry) => (
              <button
                key={entry.id}
                type="button"
                className={`select-row ${selectedEntry?.id === entry.id ? 'active' : ''}`}
                onClick={() => onSelect(entry.id)}
              >
                <div className="select-row-head">
                  <span className="badge muted mono">{entry.tool}</span>
                  <span className={`badge ${statusTone(entry.result.status)}`}>
                    {entry.result.status}
                  </span>
                </div>
                <div className="muted mono" style={{ fontSize: 11 }}>
                  {new Date(entry.ts).toLocaleTimeString()} · {compactArgs(entry.args)}
                </div>
                <div className="desktop-history-summary">{summarizeResult(entry.result)}</div>
              </button>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

function ResultBlocks({ result }: { result: DesktopToolInvocation['result'] }) {
  const stdout = typeof result.stdout === 'string' ? result.stdout : null;
  const stderr = typeof result.stderr === 'string' ? result.stderr : null;
  const content = typeof result.content === 'string' ? result.content : null;
  const entries = Array.isArray(result.entries)
    ? (result.entries as Array<{ name?: string; is_dir?: boolean; size?: number | null }>)
    : null;
  const sanitized = screenshotless(result);

  return (
    <>
      {stdout && (
        <div className="desktop-code-block">
          <div className="desktop-code-header">stdout</div>
          <pre className="mono">{stdout}</pre>
        </div>
      )}
      {stderr && (
        <div className="desktop-code-block">
          <div className="desktop-code-header">stderr</div>
          <pre className="mono">{stderr}</pre>
        </div>
      )}
      {content && (
        <div className="desktop-code-block">
          <div className="desktop-code-header">content</div>
          <pre className="mono">{content}</pre>
        </div>
      )}
      {entries && (
        <div className="desktop-code-block">
          <div className="desktop-code-header">entries</div>
          <div className="desktop-entry-list">
            {entries.slice(0, 20).map((entry, index) => (
              <div key={`${entry.name ?? 'entry'}-${index}`} className="desktop-entry-row">
                <span className={`badge ${entry.is_dir ? 'ok' : 'muted'}`}>
                  {entry.is_dir ? 'dir' : 'file'}
                </span>
                <span className="mono">{entry.name ?? 'unnamed'}</span>
                {typeof entry.size === 'number' && (
                  <span className="muted mono">{entry.size.toLocaleString()} B</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="desktop-code-block">
        <div className="desktop-code-header">result</div>
        <pre className="mono">{JSON.stringify(sanitized, null, 2)}</pre>
      </div>
    </>
  );
}
