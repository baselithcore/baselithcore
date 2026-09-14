import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { DashboardProvider } from './components/DashboardProvider';

const OVERVIEW_PAYLOAD = {
  agent: { state: 'ready', backend_started: true, stealth_enabled: false },
  counts: {
    sessions: 0,
    channels_registered: 0,
    channels_live: 0,
    skills: 0,
    cron_jobs: 0,
    paired_nodes: 0,
    workspaces: 0,
    agents: 0,
    canvas_widgets: 0,
    provider_keys_total: 0,
    provider_keys_configured: 0,
  },
  inbound: {},
  usage: {
    events_in_buffer: 0,
    total_tokens: 0,
    total_cost_usd: 0,
    avg_latency_ms: 0,
  },
  metrics_available: false,
  cron_backend: 'in-memory',
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body),
  } as Response;
}

function renderAt(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <DashboardProvider>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </DashboardProvider>
    </QueryClientProvider>
  );
}

describe('App routing', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url.includes('/dash/overview')) {
          return jsonResponse(OVERVIEW_PAYLOAD);
        }
        // SSE ticket mint and anything else: a generic OK is enough — the
        // stream itself is stubbed out in src/test/setup.ts.
        return jsonResponse({});
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the NotFound page for an unknown route, inside the app shell', async () => {
    renderAt('/this-route-does-not-exist');

    expect(await screen.findByText('Route not found')).toBeInTheDocument();
    // The shell (primary nav landmark) still renders around the routed page.
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
  });
});
