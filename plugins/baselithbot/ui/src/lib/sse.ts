import { useEffect, useRef, useState } from "react";
import type { DashboardEvent } from "./api";
import { eventsStreamUrl } from "./api";

export type SseState = "connecting" | "open" | "closed" | "error";

export function useDashboardEvents(max = 200) {
  const [events, setEvents] = useState<DashboardEvent[]>([]);
  const [state, setState] = useState<SseState>("connecting");
  const ref = useRef<EventSource | null>(null);

  useEffect(() => {
    const src = new EventSource(eventsStreamUrl, { withCredentials: true });
    ref.current = src;
    setState("connecting");

    const onMessage = (e: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(e.data) as DashboardEvent;
        setEvents((prev) => {
          const next = [...prev, parsed];
          return next.length > max ? next.slice(next.length - max) : next;
        });
      } catch {
        /* ignore malformed frames */
      }
    };

    const types = [
      "message",
      "session.created",
      "session.message",
      "session.reset",
      "session.deleted",
      "cron.removed",
      "node.token_issued",
      "node.revoked",
    ];

    src.onopen = () => setState("open");
    src.onerror = () => setState("error");
    for (const t of types) src.addEventListener(t, onMessage as EventListener);

    return () => {
      for (const t of types)
        src.removeEventListener(t, onMessage as EventListener);
      src.close();
      ref.current = null;
      setState("closed");
    };
  }, [max]);

  return { events, state } as const;
}
