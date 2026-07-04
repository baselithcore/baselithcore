# Validation & TRL Program

BaselithCore maintains a structured Verification & Validation (V&V) program
whose goal is to demonstrate — with reproducible, auditable evidence — that the
framework operates correctly **outside the laboratory**, in environments
representative of production. The program is organised around the EU
Technology Readiness Level (TRL) scale and currently targets **TRL 5:
technology validated in a relevant environment**.

## Why this exists

Test suites prove correctness in the lab (TRL 4). Funding bodies, enterprise
procurement, and regulatory assessors ask for more: measured behaviour under
realistic load, realistic failures, realistic data, and real LLM providers,
documented in a form that a third party can audit. This section is that
evidence pack.

## Program components

| Component | What it demonstrates | Where |
| --------- | -------------------- | ----- |
| [V&V Plan](vv-plan.md) | Acceptance criteria defined *before* campaigns run; traceability from requirement to evidence | This section |
| [TRL 5 Evidence Matrix](trl5-evidence-matrix.md) | Per-criterion assessment status with links to artifacts | This section |
| [Validation Campaigns](campaigns.md) | Runbook for load, soak, chaos, and agentic-evaluation campaigns | This section |
| Load / soak harness | Latency & availability vs. declared SLOs under sustained realistic traffic | `tests/load/campaign.py` |
| Chaos harness | Behaviour and recovery when infrastructure dependencies fail | `scripts/chaos_campaign.sh` |
| Agentic evaluation harness | End-to-end agent quality against a golden trajectory dataset, using **real LLM providers** | `scripts/run_prompt_regression.py` + `tests/evaluation/golden/` |
| Nightly evaluation pipeline | Continuous quality signal with a live provider, not mocks | `.github/workflows/eval-nightly.yml` |
| SLO definitions | The measurable targets campaigns are judged against | `deploy/prometheus/slo-rules.yml` |
| Campaign reports | The dated, immutable evidence artifacts themselves | `validation-reports/` (repo root) |

## Verification vs. validation

- **Verification** ("did we build it right?") is continuous and automated:
  the unit / integration / contract / chaos test suites, the strict typing
  gates, the architecture-boundary gate, and the security scanners all run in
  CI on every change. See the [V&V Plan](vv-plan.md#verification) for the
  complete inventory.
- **Validation** ("does it work in a relevant environment?") is campaign
  based: a deployed instance (staging or an equivalent environment) is
  exercised by the harnesses above and the results are compared against the
  acceptance criteria fixed in the plan. Each campaign produces a dated
  report stored under `validation-reports/`.

## TRL positioning

| TRL | Definition (EU) | BaselithCore status |
| --- | --------------- | ------------------- |
| 4 | Technology validated in lab | **Achieved** — full automated verification inventory in CI |
| 5 | Technology validated in relevant environment | **Target — execution phase**: all harnesses, runbooks, and criteria complete; pilot partner secured; remaining work is running campaigns and committing reports (see [matrix](trl5-evidence-matrix.md)) |
| 6 | Technology demonstrated in relevant environment | Future — requires sustained pilot operation |

TRL 5 is not a certificate issued by a body; it is an assessment backed by
evidence (a Technology Readiness Assessment, TRA). The
[evidence matrix](trl5-evidence-matrix.md) maps each TRL 5 criterion to the
artifact that substantiates it and its current status, so an assessor can
audit the claim criterion by criterion.
