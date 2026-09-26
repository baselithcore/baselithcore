import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getEventsStreamUrl, type DashboardEvent, type OverviewResponse } from '../lib/api';
import { collectInvalidations } from './dashboardEventRouting';

export type SseState = 'connecting' | 'open' | 'closed' | 'error';

interface OverviewContextValue {
  overview: OverviewResponse | undefined;
  overviewLoading: boolean;
  overviewFetching: boolean;
}

// Three contexts rather than one: a single value object holding events,
// connection state and the overview re-rendered every consumer (the TopBar
// on every page included) once per SSE frame. Now a component re-renders
// only when the slice it reads changes.
const OverviewContext = createContext<OverviewContextValue | null>(null);
const EventsContext = createContext<DashboardEvent[] | null>(null);
const EventStateContext = createContext<SseState | null>(null);

/** Hard cap on the in-memory event ring; the Logs page reads at most this many. */
export const MAX_BUFFERED_EVENTS = 500;
/** Frames arriving inside this window are committed as one render. */
const FLUSH_INTERVAL_MS = 150;
/** Overview poll while the SSE stream is live (events already invalidate it). */
const OVERVIEW_POLL_LIVE_MS = 30_000;
/** Overview poll while the stream is down — the only freshness source left. */
const OVERVIEW_POLL_FALLBACK_MS = 7_000;

export function DashboardProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<DashboardEvent[]>([]);
  const [eventState, setEventState] = useState<SseState>('connecting');

  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
    refetchInterval: eventState === 'open' ? OVERVIEW_POLL_LIVE_MS : OVERVIEW_POLL_FALLBACK_MS,
  });

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;
    let retryTimer: number | undefined;
    let flushTimer: number | undefined;
    let attempt = 0;
    let pending: DashboardEvent[] = [];

    const flush = () => {
      flushTimer = undefined;
      if (cancelled || pending.length === 0) return;
      const batch = pending;
      pending = [];
      setEvents((prev) => {
        const next = prev.concat(batch);
        return next.length > MAX_BUFFERED_EVENTS
          ? next.slice(next.length - MAX_BUFFERED_EVENTS)
          : next;
      });
      for (const queryKey of collectInvalidations(batch)) {
        queryClient.invalidateQueries({ queryKey });
      }
    };

    const onMessage = (e: MessageEvent<string>) => {
      let parsed: DashboardEvent;
      try {
        parsed = JSON.parse(e.data) as DashboardEvent;
      } catch {
        return; // ignore malformed frames
      }
      if (!parsed || typeof parsed !== 'object' || typeof parsed.type !== 'string') return;
      pending.push(parsed);
      if (pending.length > MAX_BUFFERED_EVENTS) {
        pending = pending.slice(pending.length - MAX_BUFFERED_EVENTS);
      }
      if (flushTimer === undefined) {
        flushTimer = window.setTimeout(flush, FLUSH_INTERVAL_MS);
      }
    };

    const connect = async () => {
      if (cancelled) return;
      setEventState('connecting');
      const streamUrl = await getEventsStreamUrl();
      if (cancelled) return;
      const src = new EventSource(streamUrl, { withCredentials: true });
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
        // A fresh ticket is minted on every reconnect: the old one is
        // single-use and already consumed.
        const delay = Math.min(30_000, 1000 * 2 ** attempt);
        attempt += 1;
        retryTimer = window.setTimeout(connect, delay);
      };

      // Backend dual-emits every event on the default "message" channel,
      // so a single listener captures every published type (wildcard).
      src.onmessage = onMessage;
    };

    void connect();

    return () => {
      cancelled = true;
      window.clearTimeout(retryTimer);
      window.clearTimeout(flushTimer);
      pending = [];
      if (source) {
        source.onmessage = null;
        source.close();
        source = null;
      }
      setEventState('closed');
    };
  }, [queryClient]);

  const overviewValue = useMemo(
    () => ({
      overview: overviewQuery.data,
      overviewLoading: overviewQuery.isLoading,
      overviewFetching: overviewQuery.isFetching,
    }),
    [overviewQuery.data, overviewQuery.isFetching, overviewQuery.isLoading]
  );

  return (
    <OverviewContext.Provider value={overviewValue}>
      <EventStateContext.Provider value={eventState}>
        <EventsContext.Provider value={events}>{children}</EventsContext.Provider>
      </EventStateContext.Provider>
    </OverviewContext.Provider>
  );
}

function required<T>(value: T | null): T {
  if (value === null) {
    throw new Error('Dashboard hooks must be used inside DashboardProvider');
  }
  return value;
}

export function useDashboardOverview() {
  const ctx = required(useContext(OverviewContext));
  return {
    data: ctx.overview,
    isLoading: ctx.overviewLoading,
    isFetching: ctx.overviewFetching,
  } as const;
}

/** Connection state only — does not re-render when events arrive. */
export function useDashboardEventState(): SseState {
  return required(useContext(EventStateContext));
}

export function useDashboardEvents(max = 200) {
  const all = required(useContext(EventsContext));
  const state = useDashboardEventState();
  const events = useMemo(() => {
    if (max <= 0 || all.length <= max) return all;
    return all.slice(all.length - max);
  }, [all, max]);

  return { events, state } as const;
}
