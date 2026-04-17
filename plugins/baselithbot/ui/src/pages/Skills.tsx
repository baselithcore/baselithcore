import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { EmptyState } from "../components/EmptyState";
import { Skeleton } from "../components/Skeleton";

const SCOPES = ["", "bundled", "managed", "workspace"] as const;

export function Skills() {
  const [scope, setScope] = useState<(typeof SCOPES)[number]>("");

  const { data, isLoading } = useQuery({
    queryKey: ["skills", scope],
    queryFn: () => api.skills(scope || undefined),
    refetchInterval: 15_000,
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <PageHeader
        eyebrow="Capabilities"
        title="Skills registry"
        description="Reusable skills available to the bot (bundled, managed, or workspace-scoped)."
        actions={
          <div className="inline">
            {SCOPES.map((s) => (
              <button
                key={s || "all"}
                type="button"
                className={`btn sm ${scope === s ? "primary" : "ghost"}`}
                onClick={() => setScope(s)}
              >
                {s || "all"}
              </button>
            ))}
          </div>
        }
      />

      {isLoading && <Skeleton height={220} />}

      {data && data.skills.length === 0 && (
        <EmptyState
          title="No skills installed"
          description="Register skills via the SkillRegistry to surface them here."
        />
      )}

      {data && data.skills.length > 0 && (
        <Panel padded={false}>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 220 }}>Name</th>
                <th>Scope</th>
                <th>Version</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {data.skills.map((s) => (
                <tr key={s.name}>
                  <td>
                    <span className="mono" style={{ color: "var(--ink-100)" }}>
                      {s.name}
                    </span>
                  </td>
                  <td>
                    <span className="badge">{s.scope}</span>
                  </td>
                  <td className="mono">{s.version}</td>
                  <td className="muted">{s.description || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}
