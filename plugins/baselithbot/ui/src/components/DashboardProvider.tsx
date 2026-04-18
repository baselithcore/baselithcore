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

const OVERVIEW_REFRESH_TYPES = new Set<string>([
  'session.created',
  'session.reset',
  'session.deleted',
  'skill.clawhub_synced',
  'skill.installed',
  'skill.rescanned',
  'skill.removed',
  'cron.removed',
  'cron.custom_registered',
  'cron.custom_updated',
  'node.token_issued',
  'node.revoked',
  'workspace.created',
  'workspace.updated',
  'workspace.deleted',
  'agent.custom_registered',
  'agent.custom_updated',
  'agent.custom_deleted',
  'channel.started',
  'channel.stopped',
  'channel.config_updated',
  'channel.config_deleted',
  'channel.inbound',
  'canvas.rendered',
  'canvas.cleared',
  'provider_keys.updated',
  'provider_keys.deleted',
]);

const SKILL_REFRESH_TYPES = new Set<string>([
  'skill.clawhub_configured',
  'skill.clawhub_synced',
  'skill.installed',
  'skill.rescanned',
  'skill.removed',
]);

const RUNTIME_REFRESH_TYPES = new Set<string>(['computer_use.updated', 'stealth.updated']);

function readSessionId(parsed: DashboardEvent): string | null {
  const payload = parsed.payload;
  if (!payload || typeof payload !== 'object') return null;
  if ('session_id' in payload && payload.session_id != null) {
    return String(payload.session_id);
  }
  if (parsed.type === 'session.created' && 'id' in payload && payload.id != null) {
    return String(payload.id);
  }
  return null;
}

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
        if (SKILL_REFRESH_TYPES.has(parsed.type)) {
          queryClient.invalidateQueries({ queryKey: ['skills'] });
        }
        if (RUNTIME_REFRESH_TYPES.has(parsed.type)) {
          queryClient.invalidateQueries({ queryKey: ['overview'] });
          queryClient.invalidateQueries({ queryKey: ['computer-use'] });
          queryClient.invalidateQueries({ queryKey: ['stealth'] });
          queryClient.invalidateQueries({ queryKey: ['audit-log'] });
        }
        if (parsed.type.startsWith('session.')) {
          queryClient.invalidateQueries({ queryKey: ['sessions'] });
          const sessionId = readSessionId(parsed);
          if (sessionId) {
            queryClient.invalidateQueries({ queryKey: ['sessionHistory', sessionId] });
          }
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

      // Backend dual-emits every event on the default "message" channel,
      // so a single listener captures every published type (wildcard).
      src.onmessage = onMessage;
    };

    connect();

    return () => {
      cancelled = true;
      window.clearTimeout(retryTimer);
      if (source) {
        source.onmessage = null;
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
