import { useState } from 'react';
import type { CronActionCatalogEntry, CustomCronPayload } from '../../../lib/api';

interface CustomCronFormProps {
  actions: CronActionCatalogEntry[];
  namePrefix: string;
  submitting: boolean;
  onSubmit: (payload: CustomCronPayload) => void;
  onCancel: () => void;
}

export function CustomCronForm({
  actions,
  namePrefix,
  submitting,
  onSubmit,
  onCancel,
}: CustomCronFormProps) {
  const [name, setName] = useState('');
  const [interval, setInterval] = useState('60');
  const [description, setDescription] = useState('');
  const [actionType, setActionType] = useState(actions[0]?.type ?? 'log');
  const [logMessage, setLogMessage] = useState('ping');
  const [logLevel, setLogLevel] = useState<'debug' | 'info' | 'warning'>('info');
  const [slashCommand, setSlashCommand] = useState('/status');
  const [webhookUrl, setWebhookUrl] = useState('https://');
  const [webhookBody, setWebhookBody] = useState('{}');
  const [webhookHeaders, setWebhookHeaders] = useState('{}');
  const [webhookTimeout, setWebhookTimeout] = useState('15');
  const [enabled, setEnabled] = useState(true);
  const [localError, setLocalError] = useState<string | null>(null);

  const handleSubmit = () => {
    setLocalError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setLocalError('Name is required.');
      return;
    }
    const secs = Number(interval);
    if (!Number.isFinite(secs) || secs < 1 || secs > 86400) {
      setLocalError('Interval must be between 1 and 86400 seconds.');
      return;
    }

    let params: Record<string, unknown>;
    try {
      if (actionType === 'log') {
        if (!logMessage.trim()) throw new Error('Message cannot be empty.');
        params = { message: logMessage, level: logLevel };
      } else if (actionType === 'chat_command') {
        if (!slashCommand.startsWith('/')) {
          throw new Error("Command must start with '/'.");
        }
        params = { command: slashCommand };
      } else if (actionType === 'http_webhook') {
        if (!webhookUrl.startsWith('http')) {
          throw new Error('URL must start with http:// or https://');
        }
        const body = webhookBody.trim() ? JSON.parse(webhookBody) : {};
        const headers = webhookHeaders.trim() ? JSON.parse(webhookHeaders) : {};
        const timeoutN = Number(webhookTimeout);
        if (!Number.isFinite(timeoutN) || timeoutN < 1 || timeoutN > 60) {
          throw new Error('Timeout must be between 1 and 60 seconds.');
        }
        params = {
          url: webhookUrl,
          body,
          headers,
          timeout_seconds: timeoutN,
        };
      } else {
        throw new Error(`Unsupported action '${actionType}'.`);
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
      return;
    }

    onSubmit({
      name: trimmedName,
      interval_seconds: secs,
      action: { type: actionType, params },
      description: description.trim(),
      enabled,
    });
  };

  return (
    <div className="stack-section" style={{ marginTop: 12 }}>
      <div className="section-label">New custom cron job</div>
      <div style={{ display: 'grid', gap: 10 }}>
        <label className="field">
          <span>Name (prefix "{namePrefix}" auto-applied if missing)</span>
          <input
            className="input"
            placeholder="my-job"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Interval (seconds, 1–86400)</span>
          <input
            className="input"
            type="number"
            min={1}
            max={86400}
            step={1}
            value={interval}
            onChange={(event) => setInterval(event.target.value)}
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

        {actionType === 'log' && (
          <>
            <label className="field">
              <span>Message</span>
              <input
                className="input"
                value={logMessage}
                onChange={(event) => setLogMessage(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Level</span>
              <select
                className="select"
                value={logLevel}
                onChange={(event) =>
                  setLogLevel(event.target.value as 'debug' | 'info' | 'warning')
                }
              >
                <option value="debug">debug</option>
                <option value="info">info</option>
                <option value="warning">warning</option>
              </select>
            </label>
          </>
        )}

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
                placeholder="https://example.com/hook"
              />
            </label>
            <label className="field">
              <span>Body (JSON)</span>
              <textarea
                className="input"
                rows={3}
                value={webhookBody}
                onChange={(event) => setWebhookBody(event.target.value)}
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

        <label className="field" style={{ flexDirection: 'row', gap: 8 }}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>Enable immediately</span>
        </label>

        {localError && (
          <div className="info-block" style={{ color: 'var(--danger, crimson)' }}>
            {localError}
          </div>
        )}

        <div className="toolbar" style={{ gap: 8 }}>
          <button type="button" className="btn" disabled={submitting} onClick={handleSubmit}>
            {submitting ? 'Creating…' : 'Create cron job'}
          </button>
          <button type="button" className="btn" disabled={submitting} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
