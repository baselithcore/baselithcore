import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';

// Testing Library's own auto-cleanup only self-registers when it finds a
// global `afterEach` (true for Jest, and for Vitest only with `test.globals:
// true`). This project imports test functions explicitly instead of using
// Vitest globals, so register cleanup here to avoid DOM leaking across tests
// within the same file.
afterEach(() => {
  cleanup();
});

// jsdom does not implement EventSource. DashboardProvider opens one
// unconditionally on mount, so any test that renders a component subscribed
// to dashboard context (directly or via Layout/TopBar) needs a stand-in that
// never dispatches events — sufficient for tests that don't assert on live
// SSE behaviour themselves.
if (typeof window !== 'undefined' && typeof window.EventSource === 'undefined') {
  class MockEventSource {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;

    onopen: (() => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: (() => void) | null = null;

    constructor(_url: string | URL, _init?: EventSourceInit) {
      // no-op: never opens, never emits — good enough for tests that don't
      // exercise the live-event stream itself.
    }

    close(): void {
      // no-op
    }
  }

  Object.defineProperty(window, 'EventSource', {
    configurable: true,
    writable: true,
    value: MockEventSource,
  });
}
