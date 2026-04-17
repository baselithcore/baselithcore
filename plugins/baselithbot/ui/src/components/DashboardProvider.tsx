import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, eventsStreamUrl, type DashboardEvent, type OverviewResponse } from '../lib/api';

export type SseState = 'connecting' | 'open' | 'closed' | 'error';

interface DashboardContextValue {
  overview: OverviewResponse | undefined;
  overviewLoading: boolean;
  overviewFetching: boolean;
  events: DashboardEvent[];
  eventState: SseState;
}

const DashboardContext = createContext<DashboardContextValue | null>(null);

const EVENT_TYPES = [
  'message',
  'session.created',
  'session.message',
  'session.reset',
  'session.deleted',
  'cron.removed',
  'node.token_issued',
  'node.revoked',
  'run.started',
  'run.step',
  'run.completed',
  'run.failed',
] as const;

const OVERVIEW_REFRESH_TYPES = new Set<string>([
  'session.created',
  'session.reset',
  'session.deleted',
  'cron.removed',
  'node.token_issued',
  'node.revoked',
]);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<DashboardEvent[]>([]);
  const [eventState, setEventState] = useState<SseState>('connecting');

  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
    refetchInterval: 7_000,
  });

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;
    let retryTimer: number | undefined;
    let attempt = 0;

    const onMessage = (e: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(e.data) as DashboardEvent;
        setEvents((prev) => {
          const next = [...prev, parsed];
          return next.length > 500 ? next.slice(next.length - 500) : next;
        });
        if (OVERVIEW_REFRESH_TYPES.has(parsed.type)) {
          queryClient.invalidateQueries({ queryKey: ['overview'] });
        }
        if (parsed.type.startsWith('run.')) {
          queryClient.invalidateQueries({ queryKey: ['runTaskLatest'] });
          queryClient.invalidateQueries({ queryKey: ['runTaskRecent'] });
          const runId =
            parsed.payload && typeof parsed.payload === 'object' && 'run_id' in parsed.payload
              ? String(parsed.payload.run_id)
              : '';
          if (runId) {
            queryClient.invalidateQueries({ queryKey: ['runTaskById', runId] });
          }
        }
      } catch {
        /* ignore malformed frames */
      }
    };

    const connect = () => {
      if (cancelled) return;
      setEventState('connecting');
      const src = new EventSource(eventsStreamUrl, { withCredentials: true });
      source = src;

      src.onopen = () => {
        if (cancelled) return;
        attempt = 0;
        setEventState('open');
      };
      src.onerror = () => {
        if (cancelled) return;
        setEventState('error');
        src.close();
        if (source === src) source = null;
        // Exponential backoff capped at 30s (1s, 2s, 4s, 8s, 16s, 30s…).
        const delay = Math.min(30_000, 1000 * 2 ** attempt);
        attempt += 1;
        retryTimer = window.setTimeout(connect, delay);
      };

      for (const type of EVENT_TYPES) {
        src.addEventListener(type, onMessage as EventListener);
      }
    };

    connect();

    return () => {
      cancelled = true;
      window.clearTimeout(retryTimer);
      if (source) {
        for (const type of EVENT_TYPES) {
          source.removeEventListener(type, onMessage as EventListener);
        }
        source.close();
        source = null;
      }
      setEventState('closed');
    };
  }, [queryClient]);

  const value = useMemo(
    () => ({
      overview: overviewQuery.data,
      overviewLoading: overviewQuery.isLoading,
      overviewFetching: overviewQuery.isFetching,
      events,
      eventState,
    }),
    [eventState, events, overviewQuery.data, overviewQuery.isFetching, overviewQuery.isLoading]
  );

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

function useDashboardContext() {
  const ctx = useContext(DashboardContext);
  if (!ctx) {
    throw new Error('Dashboard hooks must be used inside DashboardProvider');
  }
  return ctx;
}

export function useDashboardOverview() {
  const ctx = useDashboardContext();
  return {
    data: ctx.overview,
    isLoading: ctx.overviewLoading,
    isFetching: ctx.overviewFetching,
  } as const;
}

export function useDashboardEvents(max = 200) {
  const ctx = useDashboardContext();
  const events = useMemo(() => {
    if (max <= 0 || ctx.events.length <= max) return ctx.events;
    return ctx.events.slice(ctx.events.length - max);
  }, [ctx.events, max]);

  return { events, state: ctx.eventState } as const;
}
