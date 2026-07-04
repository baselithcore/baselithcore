# Validation Campaigns

Operational runbook for executing the validation campaigns defined in the
[V&V Plan](vv-plan.md) and filing their evidence. Every campaign ends with a
dated report directory committed under `validation-reports/` at the repo root.

## Prerequisites

- A running target. For staging, provision per
  [Staging Provisioning](staging.md) (Terraform or Helm). For a local
  dry-run: `docker compose up -d`.
- The target must use a **real LLM provider** for V5 evidence
  (`LLM_PROVIDER=anthropic|openai` + provider key); Ollama-backed runs count
  as dry-runs only.
- Prometheus scraping the target with `deploy/prometheus/slo-rules.yml`
  loaded (staging campaigns).
- Load tooling: `pip install -e ".[load]"`.

## Campaign 1 — Load baseline (criteria V1, V2)

```bash
python tests/load/campaign.py --profile baseline \
  --host https://staging.example.com \
  --out validation-reports/$(date +%F)-load
```

Runs the chat-heavy Locust mix (50 users, 10 min), parses the aggregate
stats, and compares them against the SLOs (availability ≥ 99.9%,
p99 < 1000 ms). Exit code is non-zero on SLO breach. The report directory
contains the raw Locust CSVs, `report.json`, and a human-readable
`report.md`.

Variants: `--profile smoke` (10 users / 2 min, pre-flight),
`--profile stress` (200 users / 15 min — capacity headroom evidence; SLO
comparison is informative, not blocking, for stress).

## Campaign 2 — Soak (criterion V3)

```bash
python tests/load/campaign.py --profile soak \
  --host https://staging.example.com \
  --out validation-reports/$(date +%F)-soak
```

Default soak: 40 users for 6h (`--duration 24h` to extend). While it runs,
capture memory-trend evidence from Prometheus
(`process_resident_memory_bytes`) — attach a screenshot or a PromQL export to
the report directory. V3 requires the V1/V2 thresholds to hold across the
whole window **and** flat memory in the final two-thirds.

## Campaign 3 — Chaos (criterion V4)

```bash
scripts/chaos_campaign.sh \
  --target http://localhost:8000 \
  --out validation-reports/$(date +%F)-chaos
```

For each dependency (default: `redis`, `qdrant`; add PostgreSQL with
`--include-postgres`, report-only): stops the Compose service, probes the API
during the outage, restarts it, and measures time-to-recovery. Judged against
V4: health endpoint stays up, no unbounded 5xx storm, recovery ≤ 60s.
Run against a Compose-managed target (staging chaos on Kubernetes can use
`kubectl delete pod` on the dependency with the same probe loop — record
equivalently in the report).

The same failure modes are unit-verified continuously in `tests/chaos/`
(deselect with `-m "not chaos"`); this campaign reproduces them against a
**deployed** instance.

## Campaign 4 — Agentic evaluation (criterion V5)

```bash
python scripts/run_prompt_regression.py \
  --capture --target https://staging.example.com \
  --api-key "$BASELITH_API_KEY" \
  --out validation-reports/$(date +%F)-eval
```

Drives every golden case in `tests/evaluation/golden/` through the deployed
`/v1/chat` endpoint, records outputs and latency, evaluates them with
`core.evaluation.regression_runner`, and fails below the 0.90 pass-rate
threshold. Replay mode (`--runs recorded.json`, no network) re-scores a prior
capture deterministically.

The same harness runs nightly in CI against a live provider
(`.github/workflows/eval-nightly.yml`); its report artifacts are the V5
trend evidence. Extend the dataset by adding YAML files to
`tests/evaluation/golden/` — schema is validated by
`tests/evaluation/test_golden_cases.py`.

## Campaign 5 — Ops rehearsal (criterion V7)

No harness — an operator who did not write the docs executes, from the
published documentation only: (1) deploy a release to staging, (2) roll back
to the previous release, (3) back up and restore PostgreSQL
(`scripts/backup-db.sh`, `scripts/restore-db.sh`, `scripts/verify-backup.sh`).
File a log noting doc gaps encountered; gaps become doc PRs.

## Campaign 6 — Pilot (criterion V6)

Partner secured (July 2026); execution window to be scheduled. Requirements
and KPIs in [V&V Plan §4 V6](vv-plan.md#v6--pilot-use) — ≥ 4 consecutive
weeks from the start date. Weekly KPI snapshots and the final pilot report
go in `validation-reports/<date>-pilot/`.

## Report conventions

Every campaign directory contains at minimum:

```text
validation-reports/<YYYY-MM-DD>-<campaign>/
├── report.md      # template below
├── report.json    # machine-readable results (where the harness emits one)
└── raw/           # harness raw output (CSVs, logs, captures)
```

`report.md` template:

```markdown
# <Campaign> — <date>

- **Target:** <URL / cluster + chart version>
- **Build under test:** <git SHA / image tag>
- **Environment:** <staging | local dry-run | CI>
- **Operator:** <who ran it>
- **Criteria exercised:** <V1, V2, ...>

## Results
| Criterion | Target | Measured | Pass |
| --------- | ------ | -------- | ---- |

## Deviations & anomalies
<anything unexpected, even if criteria passed>

## Verdict
<PASS / FAIL + one-line justification>
```

Reports are immutable once merged; corrections are filed as new reports.
