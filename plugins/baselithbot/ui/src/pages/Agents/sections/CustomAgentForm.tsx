import { useState } from 'react';
import type { AgentActionCatalogEntry, CustomAgentPayload } from '../../../lib/api';
import { buildCustomAgentPayload } from '../helpers';

interface CustomAgentFormProps {
  actions: AgentActionCatalogEntry[];
  namePrefix: string;
  submitting: boolean;
  onSubmit: (payload: CustomAgentPayload) => void;
  onCancel: () => void;
}

export function CustomAgentForm({
  actions,
  namePrefix,
  submitting,
  onSubmit,
  onCancel,
}: CustomAgentFormProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [keywordsRaw, setKeywordsRaw] = useState('');
  const [priority, setPriority] = useState('100');
  const [actionType, setActionType] = useState(actions[0]?.type ?? 'chat_command');
  const [slashCommand, setSlashCommand] = useState('/status');
  const [webhookUrl, setWebhookUrl] = useState('https://');
  const [webhookHeaders, setWebhookHeaders] = useState('{}');
  const [webhookTimeout, setWebhookTimeout] = useState('15');
  const [staticPayload, setStaticPayload] = useState('{"ok": true}');
  const [localError, setLocalError] = useState<string | null>(null);

  const handleSubmit = () => {
    setLocalError(null);
    const result = buildCustomAgentPayload({
      name,
      description,
      keywordsRaw,
      priority,
      actionType,
      slashCommand,
      webhookUrl,
      webhookHeaders,
      webhookTimeout,
      staticPayload,
    });
    if (!result.ok) {
      setLocalError(result.error);
      return;
    }
    onSubmit(result.payload);
  };

  return (
    <div className="stack-section" style={{ marginTop: 12 }}>
      <div className="section-label">New custom agent</div>
      <div style={{ display: 'grid', gap: 10 }}>
        <label className="field">
          <span>Name (prefix "{namePrefix}" auto-applied if missing)</span>
          <input
            className="input"
            placeholder="my-agent"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Description (optional)</span>
          <input
            className="input"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Keywords (comma-separated)</span>
          <input
            className="input"
            placeholder="python, bug, code"
            value={keywordsRaw}
            onChange={(event) => setKeywordsRaw(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Priority (0–10000; higher wins on tied keyword hits)</span>
          <input
            className="input"
            type="number"
            min={0}
            max={10_000}
            step={1}
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Action</span>
          <select
            className="select"
            value={actionType}
            onChange={(event) => setActionType(event.target.value)}
          >
            {actions.map((entry) => (
              <option key={entry.type} value={entry.type}>
                {entry.label} — {entry.type}
              </option>
            ))}
          </select>
        </label>

        {actionType === 'chat_command' && (
          <label className="field">
            <span>Slash command (must start with /)</span>
            <input
              className="input"
              value={slashCommand}
              onChange={(event) => setSlashCommand(event.target.value)}
              placeholder="/status"
            />
          </label>
        )}

        {actionType === 'http_webhook' && (
          <>
            <label className="field">
              <span>URL</span>
              <input
                className="input"
                value={webhookUrl}
                onChange={(event) => setWebhookUrl(event.target.value)}
                placeholder="https://example.com/agent"
              />
            </label>
            <label className="field">
              <span>Headers (JSON)</span>
              <textarea
                className="input"
                rows={2}
                value={webhookHeaders}
                onChange={(event) => setWebhookHeaders(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Timeout (seconds, 1–60)</span>
              <input
                className="input"
                type="number"
                min={1}
                max={60}
                step={1}
                value={webhookTimeout}
                onChange={(event) => setWebhookTimeout(event.target.value)}
              />
            </label>
          </>
        )}

        {actionType === 'static_response' && (
          <label className="field">
            <span>Static payload (JSON object)</span>
            <textarea
              className="input"
              rows={4}
              value={staticPayload}
              onChange={(event) => setStaticPayload(event.target.value)}
            />
          </label>
        )}

        {localError && (
          <div className="info-block" style={{ color: 'var(--danger, crimson)' }}>
            {localError}
          </div>
        )}

        <div className="toolbar" style={{ gap: 8 }}>
          <button type="button" className="btn" disabled={submitting} onClick={handleSubmit}>
            {submitting ? 'Creating…' : 'Create agent'}
          </button>
          <button type="button" className="btn" disabled={submitting} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
