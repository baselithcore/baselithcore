import { useEffect, type RefObject } from 'react';

interface OverlayA11yOptions {
  active: boolean;
  containerRef: RefObject<HTMLElement | null>;
  initialFocusRef?: RefObject<HTMLElement | null>;
  onEscape?: () => void;
  lockScroll?: boolean;
  restoreFocus?: boolean;
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter((element) => {
    if (element.matches('[aria-hidden="true"]')) return false;
    return element.getClientRects().length > 0;
  });
}

export function useOverlayA11y({
  active,
  containerRef,
  initialFocusRef,
  onEscape,
  lockScroll = false,
  restoreFocus = true,
}: OverlayA11yOptions) {
  useEffect(() => {
    if (!active) return;

    const container = containerRef.current;
    if (!container) return;

    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;

    const moveFocusInside = () => {
      const target =
        initialFocusRef?.current ?? getFocusableElements(container)[0] ?? container;
      target.focus();
    };

    const frame = window.requestAnimationFrame(moveFocusInside);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && onEscape) {
        event.preventDefault();
        onEscape();
        return;
      }

      if (event.key !== 'Tab') return;

      const focusable = getFocusableElements(container);
      if (focusable.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }

      const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      if (!activeElement || !container.contains(activeElement)) {
        event.preventDefault();
        (event.shiftKey ? focusable[focusable.length - 1] : focusable[0]).focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (event.shiftKey && activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    if (lockScroll) document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKeyDown);

    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener('keydown', onKeyDown);
      if (lockScroll) document.body.style.overflow = previousOverflow;
      if (restoreFocus && previousFocus && document.contains(previousFocus)) {
        previousFocus.focus();
      }
    };
  }, [active, containerRef, initialFocusRef, lockScroll, onEscape, restoreFocus]);
}
