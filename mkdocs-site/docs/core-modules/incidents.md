---
title: Security Incident Reporting
description: Structured incident records and reporting-deadline tracking for NIS2 Art. 23, DORA Art. 19, EU AI Act Art. 73 and GDPR Art. 33/34
---

`core/incidents/` records incidents and tracks the regulatory reporting
milestones for four regimes that can run side by side: **NIS2 (EU 2022/2555)
Art. 23**, **DORA (EU 2022/2554) Art. 19**, **EU AI Act (EU 2024/1689) Art. 73**
and **GDPR (EU 2016/679) Art. 33/34**.

The framework cannot file with the competent authority on the operator's behalf
— a national CSIRT under NIS2, the financial supervisor under DORA, the market
surveillance authority under the AI Act, the data protection supervisory
authority under GDPR. That remains the operator's action. What it does produce
is the structured record that backs each filing, with the reporting clock made
explicit, so an overdue obligation is detectable rather than silently missed.

## NIS2 reporting (Art. 23)

| Milestone               | Deadline (from awareness)      |
| ----------------------- | ------------------------------ |
| **Early warning**       | within **24h**                 |
| **Incident notification** | within **72h**               |
| **Final report**        | within **one month** of the notification |

## Design

- **Opt-in & additive.** Gated by `INCIDENT_REPORTING_ENABLED` (default off);
  no effect until enabled.
- **Sacred Core.** Domain-agnostic infrastructure, so it lives in `core/`.
- **Storage-agnostic.** `IncidentStore` is a Protocol with an in-memory
  reference implementation and a bundled SQLite store (selected per regime by
  the `*_DB_PATH` settings below); register your own durable store (e.g.
  Postgres) for production, exactly like other subsystems.
- **Auditable.** Every transition emits an `AUDIT | INCIDENT | …` log line.
- **Significance gate.** Only incidents flagged `significant` carry reporting
  deadlines; others are recorded for the incident-handling trail
  (Art. 21(2)(b)) without a regulatory clock.

## Configuration

| Setting                           | Env var                            | Default | Description                                       |
| --------------------------------- | ---------------------------------- | ------- | ------------------------------------------------- |
| `enabled`                         | `INCIDENT_REPORTING_ENABLED`       | `false` | NIS2 master switch.                               |
| `early_warning_hours`             | `INCIDENT_EARLY_WARNING_HOURS`     | `24`    | NIS2 early-warning deadline.                      |
| `notification_hours`              | `INCIDENT_NOTIFICATION_HOURS`      | `72`    | NIS2 incident-notification deadline.              |
| `final_report_days`               | `INCIDENT_FINAL_REPORT_DAYS`       | `30`    | NIS2 final-report window after notification.      |
| `dora_enabled`                    | `DORA_INCIDENT_REPORTING_ENABLED`  | `false` | DORA master switch.                               |
| `dora_initial_notification_hours` | `DORA_INITIAL_NOTIFICATION_HOURS`  | `4`     | DORA initial notification, from classification.   |
| `dora_awareness_cap_hours`        | `DORA_AWARENESS_CAP_HOURS`         | `24`    | DORA hard cap on the initial notification, from awareness. |
| `dora_intermediate_report_hours`  | `DORA_INTERMEDIATE_REPORT_HOURS`   | `72`    | DORA intermediate report, from initial notification. |
| `dora_final_report_days`          | `DORA_FINAL_REPORT_DAYS`           | `30`    | DORA final report, from intermediate report.      |
| `ai_act_enabled`                  | `AI_ACT_INCIDENT_REPORTING_ENABLED` | `false` | AI Act Art. 73 master switch.                    |
| `ai_act_complete_report_days`     | `AI_ACT_COMPLETE_REPORT_DAYS`      | `30`    | Internal SLA for the Art. 73(5) complete report.  |
| `gdpr_enabled`                    | `GDPR_BREACH_REPORTING_ENABLED`    | `false` | GDPR Art. 33/34 master switch.                    |
| `gdpr_authority_notification_hours` | `GDPR_AUTHORITY_NOTIFICATION_HOURS` | `72`  | Art. 33(1) horizon; schema-capped at 72.          |
| `gdpr_subject_communication_hours` | `GDPR_SUBJECT_COMMUNICATION_HOURS` | `72`   | Internal SLA for the Art. 34(1) communication.    |
| `incident_db_path` / `dora_db_path` / `ai_act_db_path` / `gdpr_db_path` | `INCIDENT_DB_PATH` / `DORA_DB_PATH` / `AI_ACT_INCIDENT_DB_PATH` / `GDPR_BREACH_DB_PATH` | `None` | Durable SQLite store per regime; unset keeps in-memory. |

Deadlines are configurable for stricter internal SLAs — never relax them past
the regulatory maxima. Two horizons are **not** settings at all: the AI Act
2/10/15-day report deadlines are statutory *and* category-dependent, so they are
derived in code (`report_deadline_days()`), and the GDPR 72-hour field is
schema-capped so it can only ever be tightened.

## NIS2 usage

```python
from core.incidents import IncidentSeverity, get_incident_service

svc = get_incident_service()

# 1. Open on detection — the 24h/72h clock anchors to detected_at (now here).
incident = await svc.open_incident(
    "Credential-stuffing against /admin",
    IncidentSeverity.HIGH,
    affected_systems=["admin-api"],
    affected_subjects=0,
    description="Spike of failed admin logins from a single ASN.",
)

# 2. Advance through the milestones as each filing is made.
await svc.record_early_warning(incident.id)    # within 24h
await svc.record_notification(incident.id)      # within 72h
await svc.record_final_report(incident.id)      # within one month
await svc.close_incident(incident.id)

# 3. Drive escalation: which deadlines have passed unmet?
for inc, milestone in await svc.overdue_milestones():
    alert(f"NIS2 {milestone.kind.value} overdue for incident {inc.id}")
```

Status advances **monotonically** — recording an early warning after a
notification has already been filed stamps the timestamp but never drags the
status backwards.

## DORA reporting (Art. 19)

DORA imposes a distinct clock on financial entities for **major** ICT-related
incidents. A major-incident **classification** is the gate — only once an
incident is classified as major does the reporting clock start:

| Milestone                  | Deadline                                                        |
| -------------------------- | -------------------------------------------------------------- |
| **Initial notification**   | within **4h** of classification, hard-capped at **24h** from awareness |
| **Intermediate report**    | within **72h** of the initial notification                     |
| **Final report**           | within **one month** of the intermediate report                |

Classification follows the criteria of **Commission Delegated Regulation (EU)
2024/1772**: clients/financial counterparts affected, reputational impact,
service downtime, geographical spread, data losses, criticality of services
affected, and economic impact. `DoraImpactAssessment` records which criteria are
met; `DoraClassification.is_major` applies the RTS-aligned default rule —
*critical services affected* plus **two or more** other criteria — and accepts an
explicit `major_override` when the operator's threshold analysis differs.

```python
from core.incidents import DoraImpactAssessment, get_dora_incident_service

svc = get_dora_incident_service()

# 1. Open on awareness — the 24h cap anchors to detected_at (now here).
incident = await svc.open_incident(
    "Core payment rail unreachable",
    affected_systems=["payments-api"],
    affected_clients=12000,
    description="Settlement messages failing for a primary corridor.",
)

# 2. Classify — the 4h initial-notification clock anchors to classified_at.
await svc.classify(
    incident.id,
    DoraImpactAssessment(
        critical_services_affected=True,
        clients_affected=True,
        service_downtime=True,
    ),
)

# 3. Advance through the milestones as each filing is made.
await svc.record_initial_notification(incident.id)   # within 4h
await svc.record_intermediate_report(incident.id)    # within 72h
await svc.record_final_report(incident.id)           # within one month
await svc.close_incident(incident.id)

# 4. Drive escalation: which deadlines have passed unmet?
for inc, milestone in await svc.overdue_milestones():
    alert(f"DORA {milestone.kind.value} overdue for incident {inc.id}")
```

The initial-notification deadline is the **earlier** of the 4h-from-classification
and 24h-from-awareness moments — both obligations must be satisfied. Status
advances monotonically, exactly as for the NIS2 workflow.

## AI Act serious incidents (Art. 73)

Providers of high-risk AI systems must report **serious incidents** to the market
surveillance authority. The clock is **category-dependent** — the part that is
easiest to get wrong:

| Trigger | Deadline from awareness |
| ------- | ----------------------- |
| Serious incident under Art. 3(49)(b) — critical infrastructure — or a **widespread infringement** | **2 days** (Art. 73(3)) |
| **Death** of a person | **10 days** (Art. 73(4)) |
| Every other serious incident | **15 days** (Art. 73(2)) |

When several categories apply, the **shortest** deadline governs. These
triggers are `open_incident` keyword arguments: `categories=` (a list of
`SeriousIncidentCategory`) and `widespread_infringement=True` for the
Art. 73(3) path. `serious=False` (default `True`) records the incident with
**no** reporting clock — `milestones()` is empty. The remaining record fields
are `severity=` (default `IncidentSeverity.HIGH`), `affected_persons=`,
`deployer=`, `member_state=`, `description=`, `details=` and `became_aware_at=`.
Art. 73(2)/(4) also require reporting *immediately* once the causal link to
the AI system is established (or even suspected) — ahead of the outer
deadline — which is why `record_causal_link()` exists as its own auditable step.

The Art. 3(49) categories are modelled as `SeriousIncidentCategory`:
`DEATH`, `SERIOUS_HEALTH_HARM`, `CRITICAL_INFRASTRUCTURE_DISRUPTION`,
`FUNDAMENTAL_RIGHTS_INFRINGEMENT`, `PROPERTY_OR_ENVIRONMENTAL_HARM`.

```python
from core.incidents import SeriousIncidentCategory, get_ai_act_incident_service

svc = get_ai_act_incident_service()

incident = await svc.open_incident(
    "Triage model mis-ranked a critical case",
    ai_system_id="triage-v3",              # ties back to the AI system registry
    categories=[SeriousIncidentCategory.SERIOUS_HEALTH_HARM],
    widespread_infringement=False,         # True -> the 2-day Art. 73(3) horizon
    serious=True,                          # default; False records without a clock
    affected_persons=1,
    deployer="regional-hospital-network",
    member_state="IT",
    description="Under-triage of a sepsis presentation.",
)
assert incident.deadline_days == 15

await svc.record_causal_link(incident.id)      # Art. 73(2): report immediately from here
await svc.record_report(incident.id)           # the filing itself
await svc.record_complete_report(incident.id)  # Art. 73(5) complete report
await svc.record_investigation(incident.id)    # Art. 73(6) investigation…
await svc.record_corrective_action(incident.id)  # …and corrective action
await svc.close_incident(incident.id)
```

**Art. 73(6) ordering matters**: the provider must not alter the AI system in a
way that would compromise the later evaluation of the incident's causes *before*
informing the authorities. Record the report first; the investigation and
corrective-action steps deliberately do **not** advance the status past
`REPORT_SUBMITTED`.

`AI_ACT_COMPLETE_REPORT_DAYS` (default 30) is an **internal SLA, not a statutory
deadline** — Art. 73(5) permits an initial incomplete report followed by a
complete one, but fixes no outer limit for the latter. The 2/10/15-day horizons
*are* statutory and are therefore derived in code, not exposed as settings.

Gated by `AI_ACT_INCIDENT_REPORTING_ENABLED`; durable store via
`AI_ACT_INCIDENT_DB_PATH`.

## GDPR personal data breaches (Art. 33/34)

A breach of personal data runs a **different** clock, on a different trigger,
toward a different authority — which is why an incident that satisfies NIS2
Art. 23 can still leave GDPR Art. 33 unmet.

| Obligation | Deadline | Applies when |
| ---------- | -------- | ------------ |
| **Art. 33(1)** — notify the supervisory authority | **72h** from awareness | Risk to rights and freedoms is not unlikely |
| **Art. 33(2)** — processor notifies its controller | without undue delay | This entity is the *processor* |
| **Art. 34(1)** — communicate to data subjects | without undue delay | **High** risk, and no Art. 34(3) exemption |
| **Art. 33(5)** — document in the register | always | **Every** breach, notified or not |

```python
from core.incidents import Art34Exemption, BreachRiskLevel, get_breach_service

svc = get_breach_service()

breach = await svc.record_breach(
    "Support export leaked to a misconfigured bucket",
    risk_level=BreachRiskLevel.HIGH,
    data_categories=["email", "support transcripts"],
    affected_subjects=8400,
    likely_consequences="Phishing exposure for affected users.",
    remedial_action="Bucket ACL corrected, keys rotated, CDN cache purged.",
)

await svc.notify_authority(breach.id)              # within 72h
await svc.communicate_to_subjects(breach.id)       # Art. 34(1)
await svc.close_breach(breach.id)
```

**Risk level drives the obligations.** `BreachRiskLevel.NONE` produces no
notification clock at all — but the breach is still registered, because
Art. 33(5) has no risk threshold and that register is what a supervisory
authority inspects. `HIGH` additionally raises the Art. 34 communication.

**Late is lawful, unexplained is not.** A notification past 72h must carry the
reasons for the delay (Art. 33(1)). Pass `delay_reason=`; omitting it on a late
filing is logged as a warning rather than silently accepted:

```python
await svc.notify_authority(
    breach.id, delay_reason="Forensics could not confirm exfiltration until day 4."
)
```

**Art. 34(3) exemptions** are recorded, not implied — claiming one removes the
communication milestone but keeps the claim (and its rationale) in the register,
because the controller must be able to justify it:

```python
await svc.claim_exemption(
    breach.id,
    Art34Exemption.PROTECTION_MEASURES,
    rationale="Records were AES-256 encrypted; keys were never exposed.",
)
```

Gated by `GDPR_BREACH_REPORTING_ENABLED`; durable store via
`GDPR_BREACH_DB_PATH`. `GDPR_AUTHORITY_NOTIFICATION_HOURS` is capped at 72 by
the schema — it exists to tighten an internal SLA, never to relax the statutory
maximum.

## API surface

**NIS2**

| Symbol                              | Purpose                                              |
| ----------------------------------- | ---------------------------------------------------- |
| `SecurityIncident`                  | Incident record + `milestones()` deadline computation. |
| `IncidentSeverity` / `IncidentStatus` | Severity bands and lifecycle states.               |
| `MilestoneKind` / `ReportingMilestone` | The three obligations with due/submitted/overdue.  |
| `IncidentService`                   | Open, advance, list, and detect overdue milestones.  |
| `IncidentStore` / `InMemoryIncidentStore` | Persistence Protocol + reference store.        |
| `get_incident_service()`            | Shared service (durable when `INCIDENT_DB_PATH` is set). |

**DORA**

| Symbol                              | Purpose                                              |
| ----------------------------------- | ---------------------------------------------------- |
| `DoraIncident`                      | Major-incident record + `milestones()` deadline computation. |
| `DoraImpactAssessment` / `DoraClassification` | The Art. 18 / RTS classification criteria and major determination. |
| `DoraIncidentStatus` / `DoraMilestoneKind` | Lifecycle states and the three obligations.    |
| `DoraIncidentService`               | Open, classify, advance, list, detect overdue milestones. |
| `DoraIncidentStore` / `InMemoryDoraIncidentStore` | Persistence Protocol + reference store.  |
| `get_dora_incident_service()`       | Shared service (durable when `DORA_DB_PATH` is set). |

**EU AI Act**

| Symbol                              | Purpose                                              |
| ----------------------------------- | ---------------------------------------------------- |
| `AiActSeriousIncident`              | Serious-incident record + category-derived `milestones()`. |
| `SeriousIncidentCategory`           | The Art. 3(49) categories that set the horizon.      |
| `report_deadline_days(categories, *, widespread_infringement=False)` | The 2/10/15-day derivation, usable standalone. Import it from `core.incidents.ai_act` — it is **not** re-exported from `core.incidents`. |
| `AiActIncidentStatus` / `AiActMilestoneKind` | Lifecycle states and the two report obligations. |
| `AiActIncidentService`              | Open, advance, list, detect overdue milestones.      |
| `AiActIncidentStore` / `InMemoryAiActIncidentStore` | Persistence Protocol + reference store. |
| `get_ai_act_incident_service()`     | Shared service (durable when `AI_ACT_INCIDENT_DB_PATH` is set). |

**GDPR**

| Symbol                              | Purpose                                              |
| ----------------------------------- | ---------------------------------------------------- |
| `PersonalDataBreach`                | Register entry + applicable `milestones()`.          |
| `BreachRiskLevel` / `BreachRole`    | Risk assessment and controller/processor position.   |
| `Art34Exemption`                    | The three Art. 34(3) grounds for not communicating.  |
| `BreachStatus` / `GdprMilestoneKind` | Lifecycle states and the two obligations.           |
| `BreachService`                     | Register, notify, communicate, exempt, close, detect overdue. |
| `BreachStore` / `InMemoryBreachStore` | Persistence Protocol + reference store.            |
| `get_breach_service()`              | Shared service (durable when `GDPR_BREACH_DB_PATH` is set). |

Every symbol above except `report_deadline_days` is re-exported from
`core.incidents`, alongside the per-regime `*NotFoundError` exceptions
(`IncidentNotFoundError`, `DoraIncidentNotFoundError`,
`AiActIncidentNotFoundError`, `BreachNotFoundError`). `ReportingMilestone`
and `IncidentSeverity` are shared across the regimes.

## One event, several clocks

The regimes are deliberately independent records rather than one polymorphic
incident: a compromise of an AI system holding personal data can be a NIS2
significant incident, an AI Act serious incident **and** a GDPR personal data
breach at once — three authorities, three horizons, three filings. Open one
record per applicable regime and correlate them through `details`:

```python
nis2 = await get_incident_service().open_incident(...)
await get_breach_service().record_breach(..., details={"nis2_incident_id": nis2.id})
await get_ai_act_incident_service().open_incident(
    ..., details={"nis2_incident_id": nis2.id}
)
```

Every transition across all four regimes is also written to the
[audit trail](audit-trail.md) as a structured `incident.*` event.

## Operational notes

- **Anchor the awareness timestamp to actual awareness.** The regulatory clock
  starts when the entity *became aware*, not when the record was created —
  when backfilling pass an explicit `detected_at=` (NIS2 and DORA
  `open_incident`) or `became_aware_at=` (AI Act `open_incident`, GDPR
  `record_breach`).
- **Poll `overdue_milestones()`** from a scheduled job and route hits to your
  alerting channel so a deadline cannot pass unnoticed.
- **Register a durable store** before relying on this in production; the default
  in-memory store does not survive a restart. The bundled SQLite stores
  (`core/incidents/persistence.py`, selected by the `*_DB_PATH` settings above)
  run every statement on a worker thread — one `asyncio.to_thread` hop per
  public operation, covering lock, statement, fetch, JSON decode and record
  rehydration — so incident writes never block the event loop.
