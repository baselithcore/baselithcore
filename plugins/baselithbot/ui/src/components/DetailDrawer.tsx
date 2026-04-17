import { useId, useRef, type ReactNode } from 'react';
import { Icon, paths } from '../lib/icons';
import { useOverlayA11y } from './useOverlayA11y';

interface Props {
  open: boolean;
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
}

export function DetailDrawer({ open, title, subtitle, children, onClose }: Props) {
  const ref = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  useOverlayA11y({
    active: open,
    containerRef: ref,
    initialFocusRef: closeRef,
    onEscape: onClose,
    lockScroll: true,
  });

  if (!open) return null;

  return (
    <div className="drawer-backdrop" role="presentation" onClick={onClose}>
      <aside
        ref={ref}
        className="detail-drawer"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? descriptionId : undefined}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="detail-drawer-head">
          <div>
            <div className="detail-drawer-title" id={titleId}>
              {title}
            </div>
            {subtitle && (
              <div className="detail-drawer-subtitle" id={descriptionId}>
                {subtitle}
              </div>
            )}
          </div>
          <button
            ref={closeRef}
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
