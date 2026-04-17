import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, type RunTaskResult } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { EmptyState } from "../components/EmptyState";
import { Icon, paths } from "../lib/icons";

export function RunTask() {
  const [goal, setGoal] = useState("");
  const [startUrl, setStartUrl] = useState("");
  const [maxSteps, setMaxSteps] = useState(20);
  const [extract, setExtract] = useState("");
  const [result, setResult] = useState<RunTaskResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.runTask({
        goal: goal.trim(),
        start_url: startUrl.trim() || null,
        max_steps: maxSteps,
        extract_fields: extract
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      }),
    onMutate: () => {
      setResult(null);
      setErrorMsg(null);
    },
    onSuccess: (data) => setResult(data),
    onError: (err: unknown) =>
      setErrorMsg(err instanceof Error ? err.message : String(err)),
  });

  const disabled = mutation.isPending || goal.trim().length === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <PageHeader
        eyebrow="Agent"
        title="Run an autonomous task"
        description="Dispatch the agent with a natural-language goal. The Observe → Plan → Act loop runs headless; the final trace and screenshot are shown below."
      />

      <section className="grid grid-split-1-2">
        <Panel title="Task" tag="POST /api/baselithbot/run">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!disabled) mutation.mutate();
            }}
            style={{ display: "flex", flexDirection: "column", gap: 14 }}
          >
            <div className="form-row">
              <label htmlFor="goal">Goal</label>
              <textarea
                id="goal"
                className="textarea"
                placeholder="e.g. Navigate to github.com and extract the trending repo titles"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                required
                maxLength={4000}
              />
            </div>

            <div className="form-row">
              <label htmlFor="starturl">Start URL (optional)</label>
              <input
                id="starturl"
                className="input"
                type="url"
                placeholder="https://example.com"
                value={startUrl}
                onChange={(e) => setStartUrl(e.target.value)}
              />
            </div>

            <div className="inline">
              <div className="form-row" style={{ flex: "0 0 160px" }}>
                <label htmlFor="steps">Max steps</label>
                <input
                  id="steps"
                  className="input"
                  type="number"
                  min={1}
                  max={100}
                  value={maxSteps}
                  onChange={(e) =>
                    setMaxSteps(
                      Math.max(1, Math.min(100, Number(e.target.value) || 20))
                    )
                  }
                />
              </div>
              <div className="form-row" style={{ flex: 1 }}>
                <label htmlFor="extract">Extract fields (comma separated)</label>
                <input
                  id="extract"
                  className="input"
                  placeholder="title, price, rating"
                  value={extract}
                  onChange={(e) => setExtract(e.target.value)}
                />
              </div>
            </div>

            <div className="inline" style={{ justifyContent: "flex-end" }}>
              <button
                type="button"
                className="btn ghost"
                disabled={mutation.isPending}
                onClick={() => {
                  setGoal("");
                  setStartUrl("");
                  setExtract("");
                  setMaxSteps(20);
                  setResult(null);
                  setErrorMsg(null);
                }}
              >
                Reset
              </button>
              <button
                type="submit"
                className="btn primary"
                disabled={disabled}
              >
                {mutation.isPending ? (
                  <>
                    <span className="spin">
                      <Icon path={paths.refresh} size={14} />
                    </span>
                    Running…
                  </>
                ) : (
                  <>
                    <Icon path={paths.play} size={14} />
                    Launch
                  </>
                )}
              </button>
            </div>
          </form>
        </Panel>

        <Panel title="Trace" tag={result ? `${result.steps_taken} steps` : ""}>
          {mutation.isPending && (
            <div className="empty">
              <strong>Executing Observe → Plan → Act loop</strong>
              <div className="muted">
                This can take up to a couple of minutes depending on the goal.
              </div>
            </div>
          )}
          {errorMsg && !mutation.isPending && (
            <div className="empty" style={{ color: "var(--accent-rose)" }}>
              <strong>Task failed</strong>
              <div className="muted">{errorMsg}</div>
            </div>
          )}
          {!mutation.isPending && !errorMsg && !result && (
            <EmptyState
              title="No task dispatched yet"
              description="Fill in the goal and press Launch. Once the agent finishes, its action history appears here."
            />
          )}
          {result && !mutation.isPending && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 14,
              }}
            >
              <div className="inline">
                <span
                  className={`badge ${result.success ? "ok" : "err"}`}
                >
                  {result.success ? "success" : "failed"}
                </span>
                <span className="badge">
                  steps: {result.steps_taken}
                </span>
                {result.final_url && (
                  <span className="badge muted mono">{result.final_url}</span>
                )}
              </div>
              {result.error && (
                <div
                  className="mono"
                  style={{
                    padding: 10,
                    borderRadius: "var(--radius-md)",
                    background: "rgba(251,113,133,0.08)",
                    border: "1px solid rgba(251,113,133,0.35)",
                    color: "var(--accent-rose)",
                    fontSize: 12,
                  }}
                >
                  {result.error}
                </div>
              )}
              {result.history.length > 0 && (
                <div className="trace">
                  {result.history.map((step, idx) => (
                    <div key={idx} className="step">
                      <span className="num">#{idx + 1}</span>
                      <span className="text">{step}</span>
                    </div>
                  ))}
                </div>
              )}
              {result.last_screenshot_b64 && (
                <img
                  className="screenshot"
                  alt="Final screenshot"
                  src={`data:image/png;base64,${result.last_screenshot_b64}`}
                />
              )}
            </div>
          )}
        </Panel>
      </section>
    </div>
  );
}
