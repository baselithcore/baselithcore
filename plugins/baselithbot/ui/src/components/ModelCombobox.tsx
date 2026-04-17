import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon, paths } from '../lib/icons';

interface Props {
  id?: string;
  value: string;
  options: string[];
  onChange: (next: string) => void;
  placeholder?: string;
  emptyHint?: string;
}

export function ModelCombobox({
  id,
  value,
  options,
  onChange,
  placeholder,
  emptyHint = 'No catalog entries — type a custom ID.',
}: Props) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.toLowerCase().includes(q));
  }, [options, filter]);

  const inCatalog = options.includes(value);

  return (
    <div className="combobox" ref={wrapRef}>
      <div className="combobox-input-wrap">
        <input
          id={id}
          className="input combobox-input"
          value={value}
          placeholder={placeholder}
          onChange={(e) => {
            onChange(e.target.value);
            setFilter(e.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => setOpen(true)}
        />
        <button
          type="button"
          className="combobox-toggle"
          aria-label={open ? 'Close catalog' : 'Open catalog'}
          onClick={() => {
            setFilter('');
            setOpen((v) => !v);
          }}
        >
          <Icon path={open ? paths.x : paths.menu} size={12} />
        </button>
      </div>

      {open && (
        <div className="combobox-pop" role="listbox">
          <div className="combobox-pop-head">
            <span className="combobox-pop-count">
              {visible.length} of {options.length}
            </span>
            {!inCatalog && value && <span className="badge warn">Custom ID</span>}
          </div>
          {options.length > 6 && (
            <input
              autoFocus
              className="input combobox-filter"
              placeholder="Filter…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          )}
          <ul className="combobox-list">
            {visible.length === 0 ? (
              <li className="combobox-empty">{emptyHint}</li>
            ) : (
              visible.map((opt) => {
                const active = opt === value;
                return (
                  <li key={opt}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={active}
                      className={`combobox-item ${active ? 'active' : ''}`}
                      onClick={() => {
                        onChange(opt);
                        setOpen(false);
                        setFilter('');
                      }}
                    >
                      <span className="combobox-item-name">{opt}</span>
                      {active && <Icon path={paths.check} size={12} />}
                    </button>
                  </li>
                );
              })
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
