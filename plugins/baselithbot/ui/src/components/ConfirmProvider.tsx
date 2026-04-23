import {
  createContext,
  useCallback,
  useContext,
  useId,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useOverlayA11y } from './useOverlayA11y';

type ConfirmTone = 'primary' | 'danger';

interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
}

interface PendingConfirm extends ConfirmOptions {
  resolve: (value: boolean) => void;
}

const ConfirmContext = createContext<((options: ConfirmOptions) => Promise<boolean>) | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  const close = useCallback((value: boolean) => {
    setPending((current) => {
      current?.resolve(value);
      return null;
    });
  }, []);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setPending({
        title: options.title,
        description: options.description,
        confirmLabel: options.confirmLabel ?? 'Confirm',
        cancelLabel: options.cancelLabel ?? 'Cancel',
        tone: options.tone ?? 'primary',
        resolve,
      });
    });
  }, []);

  useOverlayA11y({
    active: !!pending,
    containerRef: dialogRef,
    initialFocusRef: confirmRef,
    onEscape: () => close(false),
    lockScroll: true,
  });

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <div className="dialog-backdrop" role="presentation" onClick={() => close(false)}>
          <div
            ref={dialogRef}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={pending.description ? descriptionId : undefined}
            className="dialog-card"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="dialog-title" id={titleId}>
              {pending.title}
            </div>
            {pending.description && (
              <div className="dialog-description" id={descriptionId}>
                {pending.description}
              </div>
            )}
            <div className="dialog-actions">
              <button type="button" className="btn ghost" onClick={() => close(false)}>
                {pending.cancelLabel}
              </button>
              <button
                ref={confirmRef}
                type="button"
                className={`btn ${pending.tone === 'danger' ? 'danger' : 'primary'}`}
                onClick={() => close(true)}
              >
                {pending.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error('useConfirm must be used inside ConfirmProvider');
  }
  return ctx;
}
