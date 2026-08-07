---
title: AI Governance & Compliance
description: AI system registry, Art. 5/6 classification, Annex IV documentation, FRIA, ROPA, post-market monitoring
---

The incident and transparency subsystems cover *events* and *disclosures*.
`core/compliance/` covers the thing they attach to: **which AI systems do we
operate, in which risk category, under which role** — and the governance
artefacts that follow from that answer.

This matters because every AI Act obligation is conditional. Art. 11 technical
documentation, Art. 27 FRIA, Art. 49 registration and Art. 73 incident reporting
all attach to *a specific system in a specific category*. An organisation that
cannot enumerate its systems cannot state which duties it owes, let alone show
it met them.

Opt-in via `COMPLIANCE_ENABLED` (default off); Sacred Core, domain-agnostic.

## Layout

| Module | Regulatory anchor |
| ------ | ----------------- |
| `types.py` | Art. 3 roles, Art. 6 risk tiers, Annex I/III areas, conformity record |
| `prohibited.py` | Art. 5 prohibited practices — screened and audited |
| `classification.py` | Art. 6 classification, including the Art. 6(3) derogation logic |
| `registry.py` | The inventory; Art. 49 EU-database registration tracking |
| `risk_management.py` | Art. 9 risk management system |
| `annex_iv.py` | Art. 11 + Annex IV technical documentation |
| `instructions.py` | Art. 13 instructions for use (deployer-facing) |
| `fria.py` | Art. 27 fundamental rights impact assessment |
| `ropa.py` | GDPR Art. 30 records of processing activities |
| `dpia.py` | GDPR Art. 35/36 data protection impact assessment |
| `post_market.py` | Art. 72 post-market monitoring plan |
| `post_market_service.py` | Plan storage, observations, and the review sweep |
| `review_sweep.py` | Daily sweep over the recurring reviews (Art. 9/72, GDPR 35) |
| `profile.py` | Deployment posture check across every subsystem |
| `documents.py` / `artefact_services.py` / `persistence.py` | Services and durable SQLite stores |

## Registering a system

```python
from core.compliance import AiSystem, AnnexIIIArea, get_ai_system_registry

registry = get_ai_system_registry()
system, classification = await registry.register(
    AiSystem(
        name="cv-screener",
        version="1.4.0",
        intended_purpose="Rank job applications for a recruiter shortlist",
        annex_iii_areas=[AnnexIIIArea.EMPLOYMENT],
        provider_name="Acme",
        human_oversight_contacts=["talent-lead@acme.example"],
    )
)

assert system.risk_category.value == "high_risk"
print(classification.rationale)   # "In Annex III area(s) employment_and_worker_management."
print(classification.citations)   # ["Art. 6(2)", "Annex III"]
```

The derived category is **advisory** — the operator remains responsible for the
determination — but it is stored on the record with `classified_at`, so an
auditor sees the category actually asserted and when it last changed.

## Classification rules, in order

1. **Art. 5 wins first.** A declared prohibited practice yields `PROHIBITED`,
   whatever else is true. There is no high-risk path for a banned practice.
2. **Annex I** — a safety component of a product covered by the listed Union
   harmonisation legislation is high-risk (Art. 6(1)).
3. **Annex III** — a system in one of the eight areas is high-risk (Art. 6(2))
   unless the Art. 6(3) derogation applies.
4. **Profiling defeats the derogation.** Art. 6(3), last subparagraph: a system
   that profiles natural persons is *always* high-risk, whatever ground is
   claimed. This is the trap the module exists to close.
5. **GPAI is a separate axis**, not a rung on the ladder — Art. 53 duties, plus
   Art. 55 on systemic risk.
6. Otherwise `LIMITED_RISK` when Art. 50 is triggered, else `MINIMAL_RISK`.

Claiming the derogation does **not** remove the Art. 49 registration duty
(Art. 49(2)) — `requires_registration` stays true, and
`registry.unregistered_with_authority()` keeps the system on the pending list.

## Art. 5 prohibited practices

Eight practices, banned since 2 February 2025 with no compliance path and the
top penalty tier. `ProhibitedPractice` names them; screening is a **declaration
gate, not a detector** — whether a system performs social scoring is a question
about its purpose and deployment context that no runtime check can answer.

```python
from core.compliance import ProhibitedPractice, enforce_practices

enforce_practices("scorer", [ProhibitedPractice.SOCIAL_SCORING])
# ProhibitedPracticeError: Prohibited AI practice declared: Art. 5(1)(c) — …
```

`screen_practices()` is the non-raising variant, `enforce_practices()` raises.
Registration uses the former: it records `PROHIBITED` and audits it rather than
refusing, so an operator can inventory a system *before* deciding to retire it.
Narrow Art. 5 exemptions (medical/safety emotion inference, the law-enforcement
biometric derogations) are recorded via `exemption_rationale` — recording one
does not make it valid, it makes it reviewable.

## What do we owe?

```python
for duty in await registry.obligations(system.id):
    print(duty)
# Art. 9 — risk management system across the lifecycle
# Art. 10 — data governance and bias examination of training data
# Art. 11 + Annex IV — technical documentation
# …
```

## Art. 9 risk management

The obligation most easily mistaken for something the runtime already does.
`core/world_model/risk_assessor.py` scores the risk of an *action* the agent is
about to take; Art. 9 requires a **continuous iterative process across the whole
lifecycle**, systematically reviewed and updated.

Each `IdentifiedRisk` carries its analysis and its treatment, and a risk only
**closes** when it is treated, its residual evaluated *and accepted by a named
person*, **and** verified by testing — Art. 9(8) asks for evidence the measures
perform, so acceptance alone leaves the risk open.

```python
from core.compliance import (
    HarmCategory, IdentifiedRisk, RiskManagementSystem, RiskTreatment,
    get_risk_management_service,
)

file = RiskManagementSystem(
    system_id=system.id,
    process_description="Quarterly cycle owned by the risk board.",
    intended_purpose="Rank applications for a recruiter shortlist.",
    foreseeable_misuse="Used as the sole decision rather than a shortlist.",
    testing_regime="Shadow deployment with acceptance thresholds.",
    deployer_information="Onboarding deck plus written limitations.",
    responsible_contacts=["risk@acme.example"],
    post_market_plan_id=plan.id,          # Art. 9(2)(c) feeds from Art. 72
    risks=[IdentifiedRisk(
        description="Under-detection for atypical CVs",
        harm_categories=[HarmCategory.FUNDAMENTAL_RIGHTS],
        under_foreseeable_misuse=True,
        treatment=RiskTreatment.REDUCED,
        measures="Threshold raised; every rejection reviewed.",
        verification="Shadow run over 4 weeks; no regression.",
    )],
)
service = get_risk_management_service()
await service.save(file)
await service.review(file.id)     # raises while any risk is open
```

`review()` **refuses** while risks remain open. A review signed over untreated
or unverified risks records something that did not happen — and that record is
exactly what an auditor would rely on.

Two checks catch the common shortcuts: declaring foreseeable misuse in prose but
analysing no risk under it is reported as a gap (Art. 9(2)(b)), and a file with
no link to a post-market plan is reported as missing Art. 9(2)(c).

## Annex IV technical documentation

Nine sections, drawn up *before* placing on the market and kept at the
authorities' disposal for **10 years** (Art. 18). `draft_from_system()`
pre-fills what the registry already knows — general description, models,
oversight contacts, applied standards, declaration — and leaves the rest empty
on purpose: inventing content the operator never asserted would be worse than an
obvious gap.

```python
from core.compliance import draft_from_system, get_technical_documentation_service

doc = draft_from_system(system)
print([s.value for s in doc.missing_sections()])
# ['development_process', 'performance_metrics', 'risk_management', 'lifecycle_changes']

service = get_technical_documentation_service()
await service.set_section(doc.id, AnnexIVSection.RISK_MANAGEMENT, "…")
print(doc.to_markdown())     # undocumented sections render as "**Not documented.**"
```

Approving an incomplete document is allowed but logged as such — the framework
does not block a governance decision, it refuses to make it invisible.

**Section 5 is the Art. 9 file, section 3/4 come from the Art. 13 instructions.**
Pass them to the drafter rather than retyping their content:

```python
doc = draft_from_system(system, risk_file=risk_file, instructions=instructions)
```

## Art. 13 instructions for use

Annex IV is written for authorities. Art. 13 is a **different artefact for a
different reader**: the deployer. It is what makes the deployer's own duties
performable — nobody can assign competent human oversight under Art. 26(2) to
people who were never told the system's limitations.

Art. 13(3) fixes sixteen content elements; `missing_elements()` names the empty
ones and `issue()` refuses while any remain.

```python
from core.compliance import draft_instructions, get_instructions_service

instructions = draft_instructions(
    system, risk_file=risk_file, monitoring_plan=plan,
    provider_contact="dpo@acme.example",
)
service = get_instructions_service()
await service.save(instructions)
print(instructions.to_markdown())
await service.issue(instructions.id)      # raises while elements are missing
```

The draft fills what the framework already knows — identity, purpose, risk
circumstances from the Art. 9 file, metrics from the Art. 72 plan, oversight
contacts, and the Art. 12 log description from the audit configuration. What it
leaves empty is deliberate: how to interpret the output, what the input must
look like, what degrades accuracy. Inventing those would produce instructions
that read complete and tell the deployer nothing true.

## GDPR Art. 35/36 DPIA

A DPIA and an Art. 27 FRIA are neighbours, not duplicates, and conflating them
is how one of the two ends up unperformed: the DPIA protects **personal data**
and is owed by the *controller* to the supervisory authority; the FRIA protects
**fundamental rights** broadly and is owed by certain *deployers*. A system can
need both, one, or neither.

The gate that matters is Art. 36(1): where residual risk stays high after the
envisaged measures, **prior consultation with the supervisory authority is a
precondition for processing**, not a follow-up.

```python
from core.compliance import get_dpia_service

service = get_dpia_service()
await service.save(dpia)
completed = await service.complete(dpia.id)   # raises on missing Art. 35(7) elements

completed.may_start_processing   # False while a high residual risk is unconsulted
await service.record_prior_consultation(dpia.id)
```

`await service.blocked()` lists every assessment whose processing may not
lawfully start yet.

Art. 35(9) is satisfied either by recording the views of data subjects **or** by
recording why they were not sought — silence on both counts is reported as a
gap.

## Art. 27 FRIA

Six statutory elements. `missing_elements()` names the empty ones, and
`FriaService.complete()` **refuses** to stamp an assessment complete while any
are missing — a completion flag over empty statutory elements is a false claim
in the record.

```python
from core.compliance import FriaRisk, FundamentalRightsImpactAssessment, get_fria_service

fria = FundamentalRightsImpactAssessment(
    system_id=system.id,
    deployer="City of Example",
    processes_description="Benefit eligibility pre-screening.",
    usage_period="12 months from go-live",
    usage_frequency="Per application, ~400/day",
    affected_categories=["applicants", "dependants"],
    risks=[FriaRisk(description="Under-detection for atypical households")],
    human_oversight_measures="Caseworker reviews every negative outcome.",
    measures_if_materialised="Suspend automation, re-review manually.",
    governance_arrangements="Monthly review by the data ethics board.",
    complaint_mechanism="Published appeal route with a 10-day SLA.",
)
service = get_fria_service()
await service.save(fria)
await service.complete(fria.id)          # raises if any element is empty
await service.notify_authority(fria.id)  # Art. 27(3)
```

## GDPR Art. 30 ROPA

The first artefact a supervisory authority asks for. Controller entries
(Art. 30(1)) and processor entries (Art. 30(2)) share one model with different
required-element sets; a third-country transfer with neither safeguard nor
documented derogation is reported as incomplete.

```python
from core.compliance import ProcessingActivity, get_ropa_service

await get_ropa_service().save(
    ProcessingActivity(
        name="Support ticket triage",
        controller_name="Acme",
        controller_contact="dpo@acme.example",
        purposes=["Route tickets to the right queue"],
        data_subject_categories=["customers"],
        personal_data_categories=["email", "ticket body"],
        recipient_categories=["support staff"],
        retention_period="24 months after ticket closure",
        security_measures="Encryption at rest, RBAC, audit logging.",
        ai_system_id=system.id,
    )
)
```

`review()` stamps a periodic review — a ROPA nobody revisits goes stale
silently.

## Art. 72 post-market monitoring

Conformity assessment is a snapshot; Art. 72 is the obligation that the snapshot
keeps being true. A plan declares the metrics watched in production with their
alert thresholds; observations are evaluated as they arrive.

```python
from core.compliance import MonitoringMetric, PostMarketMonitoringPlan
from core.compliance.post_market import ThresholdDirection

plan = PostMarketMonitoringPlan(
    system_id=system.id,
    objectives="Detect accuracy drift and rising escalation rates.",
    metrics=[
        MonitoringMetric(name="accuracy", threshold=0.9,
                         direction=ThresholdDirection.LOWER_BOUND),
        MonitoringMetric(name="escalation_rate", threshold=0.15,
                         direction=ThresholdDirection.UPPER_BOUND),
    ],
    data_sources=["inference logs", "human review queue"],
    corrective_action_process="Freeze rollout, open an Art. 73 assessment.",
    responsible_contacts=["ml-ops@acme.example"],
)

observation = plan.observe("accuracy", 0.86)
if observation.is_breach:
    # A breach is the usual trigger for the Art. 73 serious-incident question.
    ...
```

An undeclared metric raises `KeyError` rather than appending silently — an
observation nobody planned for is a plan gap. `is_review_overdue()` treats a
plan that was *never* reviewed as overdue once its cadence has elapsed since
creation: "not started" is not "not due".

`PostMarketService` is what keeps the plan **alive**, which is the actual
Art. 72(1) requirement — a plan drawn up at launch and never revisited monitors
nothing:

```python
from core.compliance import get_post_market_service

service = get_post_market_service()
await service.save(plan)
observation = await service.observe(plan.id, "accuracy", 0.86)   # persisted
await service.review(plan.id)                                    # resets the cadence

await service.overdue_reviews()      # plans past their cadence, never-reviewed included
await service.open_breaches()        # (plan, observation) pairs that breached
await service.incomplete()           # plans missing an Art. 72 element
```

Observations are stored **inside** the plan, so the collected-data history — the
evidence that monitoring was actually active — survives a restart with it.
Breaches are audited as *failed* events so they stand out in the trail instead
of blending into routine telemetry.

## The review sweep

Three obligations here are **recurring**, and each fails the same way — the
artefact is produced once, nobody revisits it, and it quietly stops describing
the system it documents:

| Obligation | What goes stale |
| ---------- | --------------- |
| **Art. 9(1)** | The risk file, "regularly systematically reviewed and updated" |
| **Art. 72(1)** | The monitoring system, which must stay *active* |
| **GDPR Art. 35(11)** | The DPIA, reviewed where the risk changes |

`ComplianceReviewScheduler` polls all three daily and emits one warning per
overdue artefact **naming the article behind it**, plus a single audit record.
It also surfaces DPIAs whose processing may not lawfully start because the
Art. 36(1) prior consultation is outstanding — the one state here that is not
merely untidy but unlawful.

```python
from core.compliance import ComplianceReviewScheduler, sweep_summary

findings = await ComplianceReviewScheduler().sweep()
if sweep_summary(findings)["needs_attention"]:
    alert(findings)
```

Each subsystem is swept independently: a broken store in one must not hide the
overdue artefacts in the others. Enable it with
`COMPLIANCE_POST_MARKET_SWEEP_ENABLED`; a per-artefact `overdue_reviews()` that
nobody polls only moves the failure one step, to information that exists unread.

Bias examination for Art. 10(2)(f)/(g) and Art. 15 lives next door in
[`core/evaluation/fairness.py`](evaluation.md) — group selection rates,
demographic parity, disparate impact, equalized odds and per-group accuracy.

## Admin API

With `COMPLIANCE_ENABLED` the `api-routers` plugin mounts `/compliance`, gated by
the `compliance:manage` [capability scope](auth.md#capability-scopes-fine-grained-authorization).
These records exist to be *shown* — to a DPO, an internal auditor, a market
surveillance authority — and a Python REPL is not a surface those readers have.

| Method & path | Purpose |
| ------------- | ------- |
| `GET /compliance/systems` | List registered systems (filter by `risk_category`) |
| `POST /compliance/systems` | Register a system: screens Art. 5, derives Art. 6 |
| `GET /compliance/systems/{id}` | One system plus the obligations its category carries |
| `POST /compliance/systems/{id}/reclassify` | Re-derive the category after the facts changed |
| `POST /compliance/systems/{id}/lifecycle` | Advance the lifecycle stage |
| `GET /compliance/summary` | Inventory roll-up by category |
| `GET /compliance/pending-registration` | Open Art. 49 EU-database duties |
| `GET /compliance/documentation` | Annex IV documents, with missing sections named |
| `POST /compliance/documentation/draft` | Draft Annex IV from a registered system |
| `GET /compliance/risk-management` | Art. 9 files, flagging overdue reviews |
| `GET /compliance/risk-management/{id}` | One file, rendered as Annex IV §5 |
| `POST /compliance/risk-management/{id}/review` | Art. 9(1) review (422 if risks are open) |
| `GET /compliance/instructions` | Art. 13 instructions, missing elements named |
| `GET /compliance/instructions/{id}` | One set, rendered for the deployer |
| `POST /compliance/instructions/draft` | Draft Art. 13 from registry + risk file + plan |
| `POST /compliance/instructions/{id}/issue` | Issue to deployers (422 if incomplete) |
| `GET /compliance/dpia` | DPIAs; `blocked_only` = may not start processing |
| `POST /compliance/dpia/{id}/complete` | Complete (422 if Art. 35(7) elements missing) |
| `POST /compliance/dpia/{id}/prior-consultation` | Record the Art. 36(1) consultation |
| `GET /compliance/automated-decisions` | Art. 22 activities and their safeguard posture |
| `GET /compliance/automated-decisions/{id}/subject-information` | The Art. 15(1)(h) disclosure |
| `GET /compliance/fria` | Art. 27 assessments, with missing elements named |
| `GET /compliance/ropa` | The Art. 30 register |
| `GET /compliance/post-market` | Art. 72 plans, flagging overdue reviews |
| `POST /compliance/post-market/{id}/observe` | Record a production measurement |
| `POST /compliance/post-market/{id}/review` | Record a plan review |
| `GET /compliance/profile` | Report the deployment posture against the profile |
| `GET /compliance/audit/verify` | Verify the audit trail's hash chain |

The surface is read-heavy plus the few transitions that carry a regulatory
meaning. Authoring an Annex IV section or a FRIA is document work and belongs in
a document tool, not a JSON POST.

`POST /compliance/systems` records a declared prohibited practice as
`PROHIBITED` rather than rejecting it — inventorying a banned system is a
prerequisite to retiring it. Set `COMPLIANCE_BLOCK_PROHIBITED_PRACTICES=true` to
refuse with a 422 instead.

## Compliance profiles

The regulatory subsystems are individually opt-in, which is right for a
framework and awkward for a deployment that is genuinely in scope: a dozen
independent flags, one of which left off is invisible until an audit.

```bash
export BASELITH_COMPLIANCE_PROFILE=ai-act-high-risk
export BASELITH_COMPLIANCE_PROFILE_STRICT=true   # fail startup on any gap
```

| Profile | Requires |
| ------- | -------- |
| `off` (default) | nothing |
| `gdpr` | audit trail, privacy/DSR + retention, GDPR breach clock |
| `nis2` | audit trail, NIS2 incident clock |
| `dora` | audit trail, NIS2 + DORA clocks |
| `ai-act-limited-risk` | audit trail, Art. 50 transparency |
| `ai-act-high-risk` | the above plus the AI system registry and Art. 73 clock |
| `full` | every regime |

The AI Act profiles also require a **durable path for every artefact store** and
the review sweep. That is deliberate: Art. 18 obliges holding the documentation
at the authorities' disposal for ten years, so an artefact kept only in memory is
not an artefact — it is a computation that happened once. A profile that
reported "satisfied" over volatile stores would be the exact false assurance
these checks exist to prevent.

The profile **reports; it never turns anything on.** Silently enabling durable
audit storage, retention sweeps or incident clocks because an env var named a
posture would change where data is written and what gets deleted — behaviour the
operator never configured. Gaps are logged one line each with the article that
motivates them; strict mode raises `ComplianceProfileError` at startup instead.

```python
from core.compliance import ComplianceProfile, evaluate_profile

report = evaluate_profile(ComplianceProfile.AI_ACT_HIGH_RISK)
for gap in report.gaps:
    print(gap.setting, "→", gap.why)
```

## What this module does not do

- **It does not assess conformity.** Art. 43 is a procedure with a notified body
  or an internal-control route; `ConformityRecord` records *whether and when*
  each step happened.
- **It does not register with the EU database.** Art. 49 is an act toward the
  Commission; the registry tracks the duty and the returned id.
- **It does not judge substance.** A FRIA with all six elements filled in may
  still be a bad FRIA. Completeness is checkable; adequacy is not.
- **It is not legal advice.** `obligations_for()` is a checklist so an operator
  does not reconstruct it by hand — not a substitute for counsel.

## Durability

Every store defaults to in-memory: right for a framework, wrong for production,
where these records must outlive the process by years. Set
`COMPLIANCE_REGISTRY_DB_PATH`, `COMPLIANCE_DOCUMENTS_DB_PATH`,
`COMPLIANCE_FRIA_DB_PATH`, `COMPLIANCE_ROPA_DB_PATH`,
`COMPLIANCE_POST_MARKET_DB_PATH`, `COMPLIANCE_RISK_DB_PATH`,
`COMPLIANCE_INSTRUCTIONS_DB_PATH` and `COMPLIANCE_DPIA_DB_PATH`. Every registration,
reclassification and document write also lands in the
[audit trail](audit-trail.md) as a `compliance.*` event.
