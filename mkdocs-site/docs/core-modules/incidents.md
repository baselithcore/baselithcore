---
title: Security Incident Reporting
description: Structured incident records and NIS2 Art. 23 reporting-deadline tracking
---

`core/incidents/` records **significant security incidents** and tracks the
regulatory reporting milestones imposed by **NIS2 (EU 2022/2555) Art. 23**:

| Milestone               | Deadline (from awareness)      |
| ----------------------- | ------------------------------ |
| **Early warning**       | within **24h**                 |
| **Incident notification** | within **72h**               |
| **Final report**        | within **one month** of the notification |

The framework cannot file with a national CSIRT on the operator's behalf — that
remains the operator's action — but it produces the structured record that backs
each filing and makes the reporting clock explicit, so an overdue obligation is
detectable rather than silently missed.

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

| Setting               | Env var                          | Default | Description                              |
| --------------------- | -------------------------------- | ------- | ---------------------------------------- |
| `enabled`             | `INCIDENT_REPORTING_ENABLED`     | `false` | Master switch.                           |
| `early_warning_hours` | `INCIDENT_EARLY_WARNING_HOURS`   | `24`    | Early-warning deadline.                  |
| `notification_hours`  | `INCIDENT_NOTIFICATION_HOURS`    | `72`    | Incident-notification deadline.          |
| `final_report_days`   | `INCIDENT_FINAL_REPORT_DAYS`     | `30`    | Final-report window after notification.  |

Deadlines are configurable for stricter internal SLAs — never relax them past
the NIS2 maxima.

## Usage

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

## API surface

| Symbol                              | Purpose                                              |
| ----------------------------------- | ---------------------------------------------------- |
| `SecurityIncident`                  | Incident record + `milestones()` deadline computation. |
| `IncidentSeverity` / `IncidentStatus` | Severity bands and lifecycle states.               |
| `MilestoneKind` / `ReportingMilestone` | The three obligations with due/submitted/overdue.  |
| `IncidentService`                   | Open, advance, list, and detect overdue milestones.  |
| `IncidentStore` / `InMemoryIncidentStore` | Persistence Protocol + reference store.        |
| `get_incident_service()`            | Shared service over an in-memory store.              |

All symbols are re-exported from `core.incidents`.

## Operational notes

- **Anchor `detected_at` to actual awareness.** The regulatory clock starts when
  the entity *became aware*, not when the record was created — pass an explicit
  `detected_at` when backfilling.
- **Poll `overdue_milestones()`** from a scheduled job and route hits to your
  alerting channel so a deadline cannot pass unnoticed.
- **Register a durable store** before relying on this in production; the default
  in-memory store does not survive a restart.
