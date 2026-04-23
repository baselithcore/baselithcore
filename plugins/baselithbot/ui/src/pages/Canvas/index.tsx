import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import {
  api,
  type CanvasDispatchPayload,
  type CanvasRenderPayload,
  type CanvasWidget,
} from '../../lib/api';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { EmptyState } from '../../components/EmptyState';
import { Skeleton } from '../../components/Skeleton';
import { useToasts } from '../../components/ToastProvider';
import { formatAbsolute, formatNumber } from '../../lib/format';
import { SAMPLE_PAYLOAD } from './helpers';
import { MetaPanel } from './components';
import { CanvasWidgetView } from './sections/CanvasWidgetView';

export function Canvas() {
  const queryClient = useQueryClient();
  const toasts = useToasts();
  const [lastAction, setLastAction] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['canvas'],
    queryFn: api.canvas,
    refetchInterval: 10_000,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['canvas'] });

  const renderMutation = useMutation({
    mutationFn: (payload: CanvasRenderPayload) => api.canvasRender(payload),
    onSuccess: () => {
      toasts.push({ title: 'Canvas updated', tone: 'success' });
      refresh();
    },
    onError: (err: unknown) =>
      toasts.push({
        title: 'Render failed',
        description: err instanceof Error ? err.message : String(err),
        tone: 'error',
      }),
  });

  const clearMutation = useMutation({
    mutationFn: () => api.canvasClear(),
    onSuccess: () => {
      toasts.push({ title: 'Canvas cleared', tone: 'info' });
      refresh();
    },
    onError: (err: unknown) =>
      toasts.push({
        title: 'Clear failed',
        description: err instanceof Error ? err.message : String(err),
        tone: 'error',
      }),
  });

  const dispatchMutation = useMutation({
    mutationFn: (payload: CanvasDispatchPayload) => api.canvasDispatch(payload),
    onSuccess: (res) => {
      setLastAction(`${res.action} — ${new Date().toLocaleTimeString()}`);
      toasts.push({ title: `Dispatched ${res.action}`, tone: 'success' });
    },
    onError: (err: unknown) =>
      toasts.push({
        title: 'Dispatch failed',
        description: err instanceof Error ? err.message : String(err),
        tone: 'error',
      }),
  });

  const widgetTypes = useMemo(() => {
    if (!data) return {} as Record<string, number>;
    const counts: Record<string, number> = {};
    const walk = (widgets: CanvasWidget[]) => {
      for (const w of widgets) {
        counts[w.type] = (counts[w.type] ?? 0) + 1;
        if (w.type === 'list') walk(w.items);
      }
    };
    walk(data.widgets);
    return counts;
  }, [data]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="A2UI"
        title="Canvas snapshot"
        description="Current in-memory canvas surface with revisioned widgets rendered by Baselithbot."
        actions={
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              type="button"
              className="btn"
              onClick={() => renderMutation.mutate(SAMPLE_PAYLOAD)}
              disabled={renderMutation.isPending}
            >
              {renderMutation.isPending ? 'Rendering…' : 'Render demo widgets'}
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => clearMutation.mutate()}
              disabled={clearMutation.isPending}
            >
              {clearMutation.isPending ? 'Clearing…' : 'Clear canvas'}
            </button>
            <button type="button" className="btn ghost" onClick={refresh}>
              Refresh
            </button>
          </div>
        }
      />

      {isLoading && <Skeleton height={260} />}

      {!isLoading && !data && (
        <EmptyState
          title="Canvas unavailable"
          description="The dashboard could not fetch the current canvas snapshot."
        />
      )}

      {data && (
        <>
          <section className="grid grid-cols-4">
            <MetaPanel title="Surface ID" value={data.surface_id} />
            <MetaPanel title="Revision" value={String(data.revision)} />
            <MetaPanel title="Created" value={formatAbsolute(data.created_at)} />
            <MetaPanel
              title="Widget types"
              value={
                Object.keys(widgetTypes).length === 0
                  ? '—'
                  : Object.entries(widgetTypes)
                      .map(([t, n]) => `${t}:${n}`)
                      .join(' · ')
              }
            />
          </section>

          {lastAction && (
            <Panel title="Last dispatched action" tag="event">
              <div className="info-block mono">{lastAction}</div>
            </Panel>
          )}

          <section className="grid grid-split-2-1">
            <Panel title="Rendered widgets" tag={`${formatNumber(data.widgets.length)}`}>
              {data.widgets.length === 0 ? (
                <EmptyState
                  title="Canvas is empty"
                  description="Widget primitives appear here once the canvas surface is populated."
                />
              ) : (
                <div className="canvas-stack">
                  {data.widgets.map((widget) => (
                    <CanvasWidgetView
                      key={widget.id}
                      widget={widget}
                      depth={0}
                      onDispatch={(payload) => dispatchMutation.mutate(payload)}
                      dispatchBusy={dispatchMutation.isPending}
                    />
                  ))}
                </div>
              )}
            </Panel>

            <Panel title="Snapshot JSON" tag="raw">
              <pre className="code-block">{JSON.stringify(data, null, 2)}</pre>
            </Panel>
          </section>
        </>
      )}
    </div>
  );
}
