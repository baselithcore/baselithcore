import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Line } from "react-chartjs-2";
import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { api } from "../lib/api";
import { useDashboardEvents } from "../lib/sse";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { StatCard } from "../components/StatCard";
import { EmptyState } from "../components/EmptyState";
import { Skeleton } from "../components/Skeleton";
import { paths } from "../lib/icons";
import {
  formatCost,
  formatMs,
  formatNumber,
  formatRelative,
  truncate,
} from "../lib/format";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Filler
);

export function Overview() {
  const { data, isLoading } = useQuery({
    queryKey: ["overview"],
    queryFn: api.overview,
    refetchInterval: 5_000,
  });

  const { data: usageRecent } = useQuery({
    queryKey: ["usageRecent", 120],
    queryFn: () => api.usageRecent(120),
    refetchInterval: 15_000,
  });

  const { events } = useDashboardEvents(80);

  const chartData = useMemo(() => {
    const evs = usageRecent?.events ?? [];
    const labels = evs.map((_, i) => String(i + 1));
    return {
      labels,
      datasets: [
        {
          label: "tokens",
          data: evs.map((e) => e.total_tokens),
          borderColor: "#2ee6c4",
          backgroundColor: "rgba(46, 230, 196, 0.18)",
          pointRadius: 0,
          tension: 0.3,
          fill: true,
          yAxisID: "y",
        },
        {
          label: "latency (ms)",
          data: evs.map((e) => e.latency_ms),
          borderColor: "#a78bfa",
          backgroundColor: "rgba(167, 139, 250, 0.12)",
          pointRadius: 0,
          tension: 0.3,
          fill: true,
          yAxisID: "y1",
        },
      ],
    };
  }, [usageRecent]);

  if (isLoading || !data) {
    return (
      <div className="grid grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} height={112} />
        ))}
      </div>
    );
  }

  const c = data.counts;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <PageHeader
        eyebrow="Baselithbot"
        title="Control Plane Overview"
        description="Operational snapshot of the agent, its subsystems, and live channel + usage telemetry."
      />

      <section className="grid grid-cols-4">
        <StatCard
          label="Agent state"
          value={data.agent.state}
          sub={
            data.agent.backend_started
              ? "Playwright backend running"
              : "Backend idle"
          }
          iconPath={paths.activity}
          accent="teal"
        />
        <StatCard
          label="Sessions"
          value={formatNumber(c.sessions)}
          sub={`${c.workspaces} workspaces · ${c.agents} agents`}
          iconPath={paths.messages}
          accent="violet"
        />
        <StatCard
          label="Channels"
          value={`${c.channels_live} / ${c.channels_registered}`}
          sub="live / registered"
          iconPath={paths.cable}
          accent="cyan"
        />
        <StatCard
          label="Paired nodes"
          value={formatNumber(c.paired_nodes)}
          sub={`cron backend: ${data.cron_backend}`}
          iconPath={paths.waypoints}
          accent="amber"
        />
      </section>

      <section className="grid grid-cols-4">
        <StatCard
          label="Tokens (buffer)"
          value={formatNumber(data.usage.total_tokens)}
          sub={`${data.usage.events_in_buffer} events`}
          iconPath={paths.sparkles}
          accent="teal"
        />
        <StatCard
          label="Cost (buffer)"
          value={formatCost(data.usage.total_cost_usd)}
          sub="sum of recent usage"
          iconPath={paths.coin}
          accent="amber"
        />
        <StatCard
          label="Avg latency"
          value={formatMs(data.usage.avg_latency_ms)}
          sub="mean over buffer"
          iconPath={paths.bolt}
          accent="violet"
        />
        <StatCard
          label="Subsystems"
          value={formatNumber(c.skills)}
          sub={`skills · ${c.cron_jobs} cron jobs`}
          iconPath={paths.box}
          accent="cyan"
        />
      </section>

      <section className="grid grid-split-2-1">
        <Panel title="Usage · recent events" tag="tokens · latency">
          {(usageRecent?.events ?? []).length === 0 ? (
            <EmptyState
              title="No usage events yet"
              description="Events appear here as the agent processes requests and the UsageLedger records them."
            />
          ) : (
            <div className="chart-wrap">
              <Line
                data={chartData}
                options={{
                  responsive: true,
                  maintainAspectRatio: false,
                  interaction: { intersect: false, mode: "index" },
                  plugins: {
                    tooltip: {
                      backgroundColor: "#0f1319",
                      borderColor: "#2e3644",
                      borderWidth: 1,
                      titleColor: "#dde1ea",
                      bodyColor: "#b4bccb",
                    },
                  },
                  scales: {
                    x: {
                      ticks: { color: "#7a8396", maxTicksLimit: 6 },
                      grid: { color: "rgba(46,53,69,0.4)" },
                    },
                    y: {
                      position: "left",
                      ticks: { color: "#7a8396" },
                      grid: { color: "rgba(46,53,69,0.25)" },
                    },
                    y1: {
                      position: "right",
                      ticks: { color: "#7a8396" },
                      grid: { drawOnChartArea: false },
                    },
                  },
                }}
              />
            </div>
          )}
        </Panel>

        <Panel title="Live events" tag="sse">
          {events.length === 0 ? (
            <EmptyState
              title="Waiting for events"
              description="Dashboard actions will appear here in real time."
            />
          ) : (
            <div className="scroll">
              {events
                .slice()
                .reverse()
                .slice(0, 40)
                .map((ev, i) => (
                  <div key={`${ev.ts}-${i}`} className="event-row">
                    <span className="bullet" />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="meta">
                        <span>{ev.type}</span>
                        <span>{formatRelative(ev.ts)}</span>
                      </div>
                      <div className="body">
                        {truncate(JSON.stringify(ev.payload), 160)}
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          )}
        </Panel>
      </section>

      <section className="grid grid-cols-2">
        <InboundPanel stats={data.inbound} />
        <RosterPanel counts={c} />
      </section>
    </div>
  );
}

function InboundPanel({ stats }: { stats: Record<string, number> }) {
  const entries = Object.entries(stats).sort((a, b) => b[1] - a[1]);
  return (
    <Panel title="Inbound events" tag={`${entries.length} channels`}>
      {entries.length === 0 ? (
        <EmptyState
          title="No inbound events"
          description="Once channels start receiving webhooks, counts will appear here."
        />
      ) : (
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {entries.slice(0, 10).map(([name, count]) => (
            <li
              key={name}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "8px 12px",
                borderRadius: "var(--radius-sm)",
                fontSize: 13,
              }}
              className="mono"
            >
              <span>{name}</span>
              <span style={{ color: "var(--accent-teal)" }}>
                {formatNumber(count)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function RosterPanel({
  counts,
}: {
  counts: {
    sessions: number;
    skills: number;
    cron_jobs: number;
    paired_nodes: number;
    channels_registered: number;
    workspaces: number;
  };
}) {
  const rows = [
    { label: "Sessions", value: counts.sessions },
    { label: "Skills", value: counts.skills },
    { label: "Cron jobs", value: counts.cron_jobs },
    { label: "Paired nodes", value: counts.paired_nodes },
    { label: "Channels registered", value: counts.channels_registered },
    { label: "Workspaces", value: counts.workspaces },
  ];
  return (
    <Panel title="Subsystem roster" tag={`${rows.length}`}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
          gap: 8,
        }}
      >
        {rows.map((r) => (
          <div
            key={r.label}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              border: "1px solid var(--panel-border)",
              borderRadius: "var(--radius-sm)",
              padding: "8px 12px",
              fontSize: 13,
              background: "rgba(15,19,25,0.4)",
            }}
          >
            <span className="muted">{r.label}</span>
            <span className="mono" style={{ color: "var(--ink-100)" }}>
              {formatNumber(r.value)}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
