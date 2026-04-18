import { useState } from 'react';
import { Panel } from '../../../components/Panel';
import type { DesktopShared } from '../shared';

export function FilesystemSection({ shared }: { shared: DesktopShared }) {
  const { policy, canUse, invoke } = shared;
  const [fsPath, setFsPath] = useState('.');
  const [fsWritePath, setFsWritePath] = useState('');
  const [fsWriteContent, setFsWriteContent] = useState('');

  return (
    <Panel title="Filesystem" tag={policy.allow_filesystem ? 'enabled' : 'gated'}>
      <div className="desktop-action-card">
        <div className="desktop-action-head">
          <div>
            <strong>Scoped file access</strong>
            <p className="muted">
              Every path is resolved under the configured root. This panel exposes the same
              read, list, and write tools exported by the plugin.
            </p>
          </div>
          <span className={`badge ${policy.filesystem_root ? 'ok' : 'warn'}`}>
            {policy.filesystem_root ?? 'no root'}
          </span>
        </div>

        <p className="muted" style={{ fontSize: 12, marginBottom: 12 }}>
          Max payload {policy.filesystem_max_bytes.toLocaleString()} bytes
        </p>

        <div className="form-row">
          <label htmlFor="fspath">Path (read / list)</label>
          <div className="inline">
            <input
              id="fspath"
              className="input mono"
              value={fsPath}
              onChange={(e) => setFsPath(e.target.value)}
              style={{ flex: 1 }}
            />
            <button
              type="button"
              className="btn ghost"
              disabled={!canUse('baselithbot_fs_list', 'allow_filesystem')}
              onClick={() => invoke('baselithbot_fs_list', { path: fsPath })}
            >
              List
            </button>
            <button
              type="button"
              className="btn"
              disabled={!canUse('baselithbot_fs_read', 'allow_filesystem')}
              onClick={() => invoke('baselithbot_fs_read', { path: fsPath })}
            >
              Read
            </button>
          </div>
        </div>

        <div className="form-row">
          <label htmlFor="fswritepath">Write path</label>
          <input
            id="fswritepath"
            className="input mono"
            placeholder="notes/out.txt"
            value={fsWritePath}
            onChange={(e) => setFsWritePath(e.target.value)}
          />
        </div>

        <div className="form-row">
          <label htmlFor="fswritecontent">Content (UTF-8)</label>
          <textarea
            id="fswritecontent"
            className="textarea mono"
            rows={5}
            value={fsWriteContent}
            onChange={(e) => setFsWriteContent(e.target.value)}
          />
        </div>

        <div className="inline" style={{ justifyContent: 'flex-end' }}>
          <button
            type="button"
            className="btn primary"
            disabled={
              !canUse('baselithbot_fs_write', 'allow_filesystem') ||
              fsWritePath.trim().length === 0
            }
            onClick={() =>
              invoke('baselithbot_fs_write', {
                path: fsWritePath.trim(),
                content: fsWriteContent,
              })
            }
          >
            Write
          </button>
        </div>
      </div>
    </Panel>
  );
}
