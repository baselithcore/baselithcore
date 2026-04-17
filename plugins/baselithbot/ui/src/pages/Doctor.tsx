import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { Skeleton } from "../components/Skeleton";
import { Icon, paths } from "../lib/icons";

export function Doctor() {
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["doctor"],
    queryFn: api.doctor,
    refetchInterval: 30_000,
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <PageHeader
        eyebrow="Diagnostics"
        title="Doctor"
        description="Environment + dependency probes for the Baselithbot plugin."
        actions={
          <button
            type="button"
            className="btn sm"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <span className={isFetching ? "spin" : ""}>
              <Icon path={paths.refresh} size={12} />
            </span>
            Re-run
          </button>
        }
      />

      {isLoading && <Skeleton height={240} />}

      {data && (
        <section className="grid grid-cols-3">
          <Panel title="Platform">
            <Grid entries={Object.entries(data.platform)} />
          </Panel>
          <Panel title="Python dependencies">
            <BoolList entries={data.python_dependencies} />
          </Panel>
          <Panel title="System binaries">
            <BoolList entries={data.system_binaries} />
          </Panel>
        </section>
      )}
    </div>
  );
}

function Grid({ entries }: { entries: [string, string][] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {entries.map(([k, v]) => (
        <div
          key={k}
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "8px 12px",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--panel-border)",
            background: "rgba(15,19,25,0.4)",
            fontSize: 12,
          }}
        >
          <span className="muted">{k}</span>
          <span className="mono">{String(v)}</span>
        </div>
      ))}
    </div>
  );
}

function BoolList({ entries }: { entries: Record<string, boolean> }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {Object.entries(entries).map(([k, v]) => (
        <div
          key={k}
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "8px 12px",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--panel-border)",
            background: "rgba(15,19,25,0.4)",
            fontSize: 12,
          }}
        >
          <span className="mono">{k}</span>
          <span className={`badge ${v ? "ok" : "muted"}`}>
            {v ? "available" : "missing"}
          </span>
        </div>
      ))}
    </div>
  );
}
