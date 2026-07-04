# Verification & Validation Plan

This plan fixes the acceptance criteria for BaselithCore's TRL 5 validation
**before** campaigns are executed, defines the environments in which evidence
is collected, and establishes traceability from each criterion to the artifact
that proves it. Changes to acceptance criteria require a PR touching this file
— the git history of this document is the audit trail of the plan itself.

## 1. Scope

**System under validation:** the `baselith-core` distribution — FastAPI
backend (`core.api.factory.create_app`), orchestration loop, memory hierarchy,
LLM service layer with real providers, resilience layer, persistence
(PostgreSQL, Qdrant, Redis/FalkorDB) — deployed as a realistic composition
(Docker Compose or the Helm chart), **not** as in-process test fixtures.

**Out of scope for TRL 5:** optional plugins beyond the official set,
multi-region deployment, and formal penetration testing (tracked as TRL 6+
work in the [evidence matrix](trl5-evidence-matrix.md)).

## 2. Verification

Verification is fully automated and runs in CI on every push/PR
(`.github/workflows/ci.yml`). It establishes the TRL 4 baseline.

| Gate | Tooling | Pass condition |
| ---- | ------- | -------------- |
| Unit / integration / contract / chaos suites | pytest (`tests/`, asyncio_mode=auto) | 100% pass; coverage ≥ 65% (`--cov-fail-under=65`) |
| Lint & format | Ruff | zero findings |
| Typing (core) | mypy | zero errors |
| Typing (official plugins) | `scripts/check_official_plugin_typing.py` | strict, zero errors |
| Typing (resilience) | `scripts/check_core_resilience_typing.py` | strict, zero errors |
| Architecture invariant | `scripts/check_architecture_boundaries.py` | Sacred Core rule holds |
| Plugin integrity | `scripts/check_plugin_integrity.py` | manifest `integrity_sha256` matches source |
| Packaging | `scripts/check_distribution_artifacts.py` | wheel/sdist invariants hold |
| SAST | Bandit, Semgrep | no medium+ findings in `core/` |
| Dependency audit | pip-audit (advisory in CI; strict weekly via `dependency-audit.yml`) | weekly run green |
| SBOM | CycloneDX | artifact produced per build |
| Container/filesystem scan | Trivy (SARIF → Security tab) | reviewed, no unaccepted HIGH/CRITICAL |

Full-suite invocation (both repos): `pytest tests/ -o addopts=""`.

## 3. Validation environments

| Environment | Composition | Role |
| ----------- | ----------- | ---- |
| **Local lab** | `docker compose up -d` (api, worker, postgres, redis, qdrant, ollama) | Campaign dry-runs; TRL 4 |
| **CI ephemeral** | GitHub Actions service containers + live provider key | Nightly agentic evaluation (`eval-nightly.yml`) |
| **Staging** *(relevant environment)* | Helm chart with `values-staging.yaml` on a Kubernetes cluster; real LLM provider credentials; Prometheus with `slo-rules.yml` loaded | Load / soak / chaos campaigns; the environment TRL 5 evidence is collected in |
| **Pilot** | Staging-equivalent instance used by an external partner or an organisationally distinct user group | User-validation KPIs (criterion V6) |

A **relevant environment** for this system is defined as: containerised
deployment on infrastructure separate from developer machines; real network
boundaries; production-representative dependency versions; real (non-mock)
LLM provider; realistic data volumes; and observability (metrics + SLO rules)
active — so that measurements are made the same way they would be in
production.

## 4. Validation acceptance criteria

Fixed targets. A campaign passes only if every criterion it exercises holds.

### V1 — Availability under load

99.9% of HTTP requests succeed (non-5xx) during a sustained baseline load
campaign. Source of truth: `deploy/prometheus/slo-rules.yml` availability SLO.
Measured by `tests/load/campaign.py` (aggregate failure ratio ≤ 0.001).

### V2 — Latency under load

99% of requests complete in < 1s during the baseline profile (health/chat/
feedback mix at 50 concurrent users). Source: latency SLO in
`slo-rules.yml`. Measured client-side by the load harness (p99 ≤ 1000 ms).

### V3 — Endurance (soak)

Under the soak profile (sustained moderate concurrency, ≥ 6h): no crash, no
restart, availability and latency criteria (V1/V2) hold over the whole
window, and no monotonic memory growth (RSS slope over the final 2/3 of the
window ≈ 0, judged from Prometheus process metrics).

### V4 — Resilience under dependency failure (chaos)

With one infrastructure dependency (Redis, Qdrant) stopped for 30s under
light traffic: the API keeps answering health probes, does not emit an
unbounded 5xx storm (circuit breakers fast-fail or degrade), and returns to
full V1 behaviour within **60s** of the dependency returning. Measured by
`scripts/chaos_campaign.sh`. PostgreSQL outage is exercised in report-only
mode (documented degradation, no recovery-time criterion at TRL 5).

### V5 — Agentic quality with a real provider

Trajectory pass-rate ≥ **0.90** (the `DEFAULT_PASS_THRESHOLD` of
`core.evaluation.regression_runner`) on the golden dataset
`tests/evaluation/golden/`, captured end-to-end through the deployed HTTP API
with a real LLM provider. Measured by `scripts/run_prompt_regression.py`;
runs nightly in CI and on demand against staging.

### V6 — Pilot use

≥ 4 consecutive weeks of pilot operation by users organisationally distinct
from the development team, with collected KPIs: task success rate ≥ 80%,
human-in-the-loop intervention rate, incident count (target: zero Sev-1),
cost per task. Evidence: pilot report under `validation-reports/`.

### V7 — Operability

A release can be deployed, rolled back, and restored from backup following
only the published documentation ([Deployment](../advanced/deployment.md),
[Kubernetes](../advanced/kubernetes.md), [Runbooks](../advanced/runbooks.md)),
by an operator who did not write it. Evidence: executed-runbook log in the
campaign report.

## 5. Traceability

| Criterion | Campaign | Harness | Evidence artifact |
| --------- | -------- | ------- | ----------------- |
| V1, V2 | Load baseline / stress | `tests/load/campaign.py` | `validation-reports/<date>-load/` |
| V3 | Soak | `tests/load/campaign.py --profile soak` | `validation-reports/<date>-soak/` |
| V4 | Chaos | `scripts/chaos_campaign.sh` | `validation-reports/<date>-chaos/` |
| V5 | Agentic eval | `scripts/run_prompt_regression.py` | nightly CI artifacts + `validation-reports/<date>-eval/` |
| V6 | Pilot | manual + telemetry | `validation-reports/<date>-pilot/` |
| V7 | Ops rehearsal | runbooks | `validation-reports/<date>-ops/` |

Campaign execution procedure: [Validation Campaigns](campaigns.md).
Criterion-by-criterion status: [TRL 5 Evidence Matrix](trl5-evidence-matrix.md).

## 6. Roles and cadence

- **Verification** — every push (CI), blocking.
- **Agentic eval (V5)** — nightly (CI, live provider), plus before every
  release against staging.
- **Load/soak/chaos (V1–V4)** — per minor release, and after any change to
  the resilience layer, the LLM service layer, or the persistence layer.
- **Pilot (V6)** — continuous during the pilot window; KPIs reviewed weekly.
- Campaign reports are committed to `validation-reports/` (immutable once
  merged; corrections are new reports, not edits).
