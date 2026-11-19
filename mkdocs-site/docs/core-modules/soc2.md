---
title: SOC 2 Readiness
description: Scopes, controls, hash-anchored evidence and an auditor's package against the AICPA Trust Services Criteria
---

`baselith/core/compliance/soc2/` prepares an organisation for a **SOC 2**
attestation. SOC 2 is not a statute with a clock: an independent CPA firm issues
a report over a service organisation's controls, against criteria the AICPA
publishes (the Trust Services Criteria). So the framework does two things and
refuses a third.

- It **records** the organisation's own controls, mapped to the criteria they
  address, and **dated evidence** that each control operated.
- It **answers the readiness question** an audit begins with: for every
  criterion in scope, can the organisation show a control operating?
- It **never states that a control is effective.** That is the auditor's
  opinion, and the package it produces says so on its first line.

## Vocabulary

| Term | Meaning |
| ---- | ------- |
| **Scope** (`Soc2Scope`) | One engagement: the categories covered, Type I or II, the period. Security is always in scope; Availability, Confidentiality, Processing integrity and Privacy are optional. |
| **Type I** | The auditor examines whether the controls are suitably *designed* as of one date. |
| **Type II** | The auditor examines whether they *operated effectively* over a period, typically three to twelve months, by sampling evidence. |
| **Control** (`Soc2Control`) | One of the organisation's own statements of what it does, with an owner and a frequency, mapped to one or more criteria. It is not a copy of the AICPA text. |
| **Evidence** (`Soc2Evidence`) | A dated, hashed piece of proof that a control operated, or did not. Automatic (from a collector) or manual. |

The catalogue (`CRITERIA`) holds the criteria **identifiers** (`CC6.1`, `A1.2`,
`P8.1`, 61 in all) with short descriptive titles written here. The criteria
text is AICPA's and is not reproduced; the auditor works from the publication.

## Evidence that cannot be swapped

Recording evidence writes its SHA-256 to the [audit trail](audit-trail.md) as a
`compliance.assessment` record (`regime: soc2`, action `soc2_evidence`). The
trail is hash-chained, so an artefact whose digest was anchored when it was
recorded cannot be replaced by a better one before the audit without the swap
being visible.

- **Automatic evidence** is hashed over the canonical JSON of what the
  collector found.
- **Manual evidence** is hashed over the artefact: a `content_sha256` the
  operator computed, else the text passed as `content` (hashed, never stored),
  else the record's own title, summary and reference — which fixes what was
  *said*, not the document behind a link.

Every change to a scope or control, and every export of the package, is on the
trail too.

## Collectors

A collector reads something the platform already knows and returns an
`EvidenceDraft`. A control lists the collectors that feed it by name; each
`Soc2Service.collect()` attaches one piece of evidence to every active control
that lists a collector.

| Collector | Reads | Speaks to |
| --------- | ----- | --------- |
| `audit_chain` | Recomputes every link of the audit trail | CC7.2, CC4.1 |
| `incident_register` | State and overdue obligations of the four incident registers | CC7.3-CC7.5 |
| `declared_posture` | The declared compliance profile against the running configuration | CC4.1, CC5.2 |
| `vendor_register` | The ICT third-party register and its concentration view | CC9.2 |
| `plugin_integrity` | Each deployed plugin's hash against the signature its manifest declares | CC8.1, CC6.8 |

The registry is a seam, not a closed list. Core ships collectors only for what
`baselith/core/` owns; a plugin that owns other evidence (an access review, a
change log) calls `register_evidence_collector(name, run, description=...,
criteria=(...))` at start-up. Core never imports a plugin. Access to the platform
(CC6) is the obvious case: the identity store belongs to a plugin, so its
evidence is collected there.

**A collector reports what it found, not what would look good.** A broken audit
chain, an overdue incident obligation, an unmet posture requirement or a missing
durable audit trail come back as an `exception` outcome. A collector that cannot
run at all is recorded as an exception on the controls that rely on it, never
skipped: evidence that silently stopped arriving would otherwise read as merely
old.

`starter_controls(owner)` returns five draft controls, one per built-in
collector. They are drafts of the organisation's own statements, not a
prescribed set.

## Readiness

`assess_scope` is pure: controls and evidence in, statuses out, no clock of its
own. A control's status, worst first:

| Status | Meaning |
| ------ | ------- |
| `exception` | The latest evidence reports a deviation, not yet followed by evidence that it operated again. |
| `untested` | No evidence in the examined window. |
| `intermittent` | (Type II) evidence exists but covers fewer than 75% of the expected intervals of the period. |
| `stale` | The latest evidence is older than 1.5 times the control's frequency. |
| `covered` | Evidence in the window, recent enough, latest not a deviation. |

A **criterion** takes the worst status among the active controls mapped to it,
and is `uncovered` when none is mapped. One control with a gap is a gap even if
a second is fine: a readiness view that rounds up is the wrong place to be
generous.

Type I judges the design as of the scope's date and uses all evidence up to it,
with no coverage ratio. Type II examines `[period_start, period_end]` (or up to
now while the period is open) and computes coverage: the share of
frequency-length intervals that hold at least one piece of evidence. Event-driven
controls have no schedule, so no staleness and no coverage.

## The auditor's package

`build_package(service, scope_id)` assembles the scope, the criteria matrix, the
controls with their evidence index (digest, kind, outcome, where it lives), and
the audit trail's current head hash, then hashes itself and records the export
on the trail with that digest. `render_markdown(package)` renders it as a
request list. Evidence *content* is not inlined: the package is a map to the
evidence, not a second copy.

## Durability and configuration

The service is in-memory until `COMPLIANCE_SOC2_DB_PATH` names a file; then all
three kinds (scopes, controls, evidence) live in one SQLite file, on the same
store base as the other compliance registers. Set it before the first
engagement: evidence recorded over a Type II period is worth exactly as much as
its survival to the audit.

```python
from datetime import date

from baselith.core.compliance.soc2 import (
    AttestationType, build_package, get_soc2_service,
)

service = get_soc2_service()
scope = await service.create_scope(
    "SOC 2 Type II, FY2027",
    attestation_type=AttestationType.TYPE_II,
    period_start=date(2027, 1, 1),
)
await service.seed_starter_controls("alice")
await service.collect()          # run daily from a scheduler
_, criteria, controls, summary = await service.readiness(scope.id)
package = await build_package(service, scope.id)
```

A Type II period needs evidence at every interval, so `collect()` has to run on
a schedule; nothing in the framework calls it on its own.

## What this module does not do

- **It does not attest, and does not say a control is effective.**
- **It does not collect evidence the platform does not own.** Cloud, HR and
  vendor-management evidence is recorded by hand, or by a collector a plugin
  registers.
- **It does not test controls.** Sampling and testing the operating
  effectiveness of a control is the auditor's work; deviations are recorded as
  `exception` evidence.
- **It does not cover the criteria text or points of focus.** Identifiers and
  short titles only.
