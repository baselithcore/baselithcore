import { useEffect, useRef, type ReactNode } from 'react';
import { Icon, paths } from '../lib/icons';

interface Props {
  open: boolean;
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
}

export function DetailDrawer({ open, title, subtitle, children, onClose }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    ref.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        ref={ref}
        className="detail-drawer"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="detail-drawer-head">
          <div>
            <div className="detail-drawer-title">{title}</div>
            {subtitle && <div className="detail-drawer-subtitle">{subtitle}</div>}
          </div>
          <button
            type="button"
            className="drawer-close"
            aria-label="Close details"
            onClick={onClose}
          >
            <Icon path={paths.x} size={16} />
          </button>
        </div>
        <div className="detail-drawer-body">{children}</div>
      </aside>
    </div>
  );
}
