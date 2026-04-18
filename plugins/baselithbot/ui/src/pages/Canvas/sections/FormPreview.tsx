import { useState } from 'react';
import type { CanvasWidget } from '../../../lib/api';

interface FormPreviewProps {
  widget: Extract<CanvasWidget, { type: 'form' }>;
  onSubmit: (values: Record<string, unknown>) => void;
  busy: boolean;
}

export function FormPreview({ widget, onSubmit, busy }: FormPreviewProps) {
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(widget.fields.map((f) => [f.name, f.default ?? '']))
  );

  return (
    <form
      className="canvas-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(values);
      }}
    >
      {widget.fields.map((field) => (
        <label key={field.name} className="canvas-form-row">
          <span className="meta-label">
            {field.label || field.name}
            {field.required ? ' *' : ''}
          </span>
          {field.type === 'select' ? (
            <select
              className="select"
              value={String(values[field.name] ?? '')}
              onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
              required={field.required}
            >
              <option value="">—</option>
              {field.options.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          ) : field.type === 'checkbox' ? (
            <input
              type="checkbox"
              checked={Boolean(values[field.name])}
              onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.checked }))}
            />
          ) : (
            <input
              type={
                field.type === 'password'
                  ? 'password'
                  : field.type === 'email'
                    ? 'email'
                    : field.type === 'number'
                      ? 'number'
                      : 'text'
              }
              className="input"
              value={String(values[field.name] ?? '')}
              onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
              required={field.required}
            />
          )}
        </label>
      ))}
      <button type="submit" className="btn primary" disabled={busy}>
        {busy ? 'Submitting…' : widget.title || 'Submit'}
      </button>
    </form>
  );
}
