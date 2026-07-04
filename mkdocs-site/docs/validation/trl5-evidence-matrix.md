# TRL 5 Evidence Matrix

Technology Readiness Assessment (TRA) working matrix for the claim
**"BaselithCore is validated in a relevant environment" (TRL 5, EU scale)**.
Each row is one criterion an assessor will probe, the evidence that
substantiates it, and its current status.

Status legend: ✅ evidence exists and is linked · 🔶 harness/process in place,
evidence being accumulated · ⬜ not started.

## A. Baseline: TRL 4 (validated in lab)

| # | Criterion | Evidence | Status |
| - | --------- | -------- | ------ |
| A1 | Components integrated and tested together | 3,100+ automated tests: unit, integration (`tests/integration/`), contract (`tests/contracts/`), fault-injection (`tests/chaos/`); coverage gate ≥ 65% | ✅ |
| A2 | Reproducible builds & packaging | wheel/sdist invariants gate, SBOM (CycloneDX) per build, release container images (`release-image.yml`) | ✅ |
| A3 | Quality processes enforced | CI gates: Ruff, mypy (3 gates incl. strict resilience/plugins), architecture-boundary check, plugin integrity, Bandit/Semgrep/Trivy | ✅ |
| A4 | Documented architecture & APIs | this documentation site; OpenAPI export (`scripts/export_openapi.py`) | ✅ |

## B. TRL 5 criteria

| # | Criterion | Evidence expected | Artifact | Status |
| - | --------- | ----------------- | -------- | ------ |
| B1 | **Relevant environment defined and reproducible** | Definition of the environment + IaC to stand it up | [V&V Plan §3](vv-plan.md#3-validation-environments); [Staging Provisioning](staging.md) (Terraform module + `terraform.tfvars.staging.example` + `values-staging.yaml`, chart lint/render verified) | 🔶 definition + IaC + runbook done; persistent instance to be provisioned (B1 acceptance = green staging smoke report) |
| B2 | **Measurable targets fixed before testing** | SLOs and acceptance criteria under version control, dated before campaign reports | [V&V Plan §4](vv-plan.md#4-validation-acceptance-criteria) (V1–V7); `deploy/prometheus/slo-rules.yml` | ✅ |
| B3 | **Performance validated against targets in the relevant environment** (V1, V2) | Load-campaign reports: measured availability/p99 vs SLO | `tests/load/campaign.py` harness; reports in `validation-reports/` | 🔶 harness ready; first staging campaign pending |
| B4 | **Endurance demonstrated** (V3) | ≥ 6h soak report, no degradation/leak | `tests/load/campaign.py --profile soak` | 🔶 |
| B5 | **Resilience under realistic faults** (V4) | Chaos-campaign report: behaviour during and after dependency outage | `scripts/chaos_campaign.sh`; lab-level fault-injection already automated in `tests/chaos/` | 🔶 lab automated; live campaign pending |
| B6 | **End-to-end quality with real external services** (V5) | Agentic-eval reports with a live LLM provider (not mocks), pass-rate ≥ 0.90 | golden dataset `tests/evaluation/golden/`; `scripts/run_prompt_regression.py`; nightly `eval-nightly.yml` with report artifacts | 🔶 harness ready; nightly deliberately deferred (awaits `ANTHROPIC_API_KEY` repo secret — no-ops safely until then); interim evidence = dated manual capture runs against staging |
| B7 | **Validation by users outside the dev team** (V6) | Pilot report: ≥ 4 weeks, KPIs (task success ≥ 80%, interventions, incidents, cost/task) | `validation-reports/<date>-pilot/` | 🔶 **pilot partner secured (July 2026)**; execution window to be scheduled — closure of the TRL 5 claim tracks its start date (+ ≥ 4 weeks) |
| B8 | **Operability demonstrated** (V7) | Executed-runbook log: deploy, rollback, backup-restore performed from docs alone | [Runbooks](../advanced/runbooks.md); backup/restore scripts (`scripts/backup-db.sh`, `scripts/verify-backup.sh`); rehearsal log pending | 🔶 |
| B9 | **Security posture assessed** | SAST + dependency + container scanning continuous; strict weekly dependency audit; documented SSRF/plugin-integrity/secrets controls | CI security jobs; `dependency-audit.yml`; [Security](../advanced/security.md) | ✅ (pen-test deferred to TRL 6) |
| B10 | **Traceability requirement → test → result** | Matrix linking criteria to campaigns to artifacts | [V&V Plan §5](vv-plan.md#5-traceability); this matrix | ✅ |

## C. Assessment summary

- **TRL 4: achieved.** Rows A1–A4 are fully evidenced by CI on every commit.
- **TRL 5: in progress — development complete, execution remaining.** Every
  criterion has an executable harness and a written runbook (B1–B6, B8), and
  the pilot partner is secured (B7). No further engineering work is required:
  each 🔶 row converts to ✅ by *running* the existing harnesses and
  committing the dated reports.
- **Critical path:** provision staging per
  [Staging Provisioning](staging.md) (~½ day on an existing cluster), then
  the campaign sequence B3–B6 + B8 (~2 effective days, mostly unattended
  soak time). The pilot (B7) dictates the closing date: the TRL 5 claim
  completes ≥ 4 weeks after its start.

## D. What an assessor should audit

1. This matrix, then the [V&V Plan](vv-plan.md) — confirm criteria predate
   reports (git history).
2. One load report and one chaos report under `validation-reports/` —
   confirm measured numbers meet V1–V4.
3. The nightly eval workflow run history — confirm V5 trend with a live
   provider.
4. The pilot report — confirm V6 KPIs and the independence of the user group.
