---
title: Security Incident Reporting
description: Structured incident records and NIS2 Art. 23 / DORA Art. 19 reporting-deadline tracking
---

`core/incidents/` records **significant security incidents** and tracks the
regulatory reporting milestones for two regimes that can run side by side:
**NIS2 (EU 2022/2555) Art. 23** and **DORA (EU 2022/2554) Art. 19**.

The framework cannot file with the competent authority (a national CSIRT under
NIS2, or the financial supervisor under DORA) on the operator's behalf — that
remains the operator's action — but it produces the structured record that backs
each filing and makes the reporting clock explicit, so an overdue obligation is
detectable rather than silently missed.

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
  reference implementation; register a durable store (e.g. Postgres) for
  production, exactly like other subsystems.
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

Deadlines are configurable for stricter internal SLAs — never relax them past
the NIS2/DORA maxima.

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

## API surface

**NIS2**

| Symbol                              | Purpose                                              |
| ----------------------------------- | ---------------------------------------------------- |
| `SecurityIncident`                  | Incident record + `milestones()` deadline computation. |
| `IncidentSeverity` / `IncidentStatus` | Severity bands and lifecycle states.               |
| `MilestoneKind` / `ReportingMilestone` | The three obligations with due/submitted/overdue.  |
| `IncidentService`                   | Open, advance, list, and detect overdue milestones.  |
| `IncidentStore` / `InMemoryIncidentStore` | Persistence Protocol + reference store.        |
| `get_incident_service()`            | Shared service over an in-memory store.              |

**DORA**

| Symbol                              | Purpose                                              |
| ----------------------------------- | ---------------------------------------------------- |
| `DoraIncident`                      | Major-incident record + `milestones()` deadline computation. |
| `DoraImpactAssessment` / `DoraClassification` | The Art. 18 / RTS classification criteria and major determination. |
| `DoraIncidentStatus` / `DoraMilestoneKind` | Lifecycle states and the three obligations.    |
| `DoraIncidentService`               | Open, classify, advance, list, detect overdue milestones. |
| `DoraIncidentStore` / `InMemoryDoraIncidentStore` | Persistence Protocol + reference store.  |
| `get_dora_incident_service()`       | Shared service over an in-memory store.              |

All symbols are re-exported from `core.incidents`. `ReportingMilestone` and
`IncidentSeverity` are shared across both regimes.

## Operational notes

- **Anchor `detected_at` to actual awareness.** The regulatory clock starts when
  the entity *became aware*, not when the record was created — pass an explicit
  `detected_at` when backfilling.
- **Poll `overdue_milestones()`** from a scheduled job and route hits to your
  alerting channel so a deadline cannot pass unnoticed.
- **Register a durable store** before relying on this in production; the default
  in-memory store does not survive a restart.
