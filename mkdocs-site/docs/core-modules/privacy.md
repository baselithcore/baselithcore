---
title: Privacy & Data-Subject Requests
description: GDPR access, portability, erasure, and retention across data providers
---

The `core/privacy` module is a **data-subject-request (DSR) framework** for the
GDPR Chapter III rights — access and portability (Art. 15/20), rectification
(Art. 16), erasure (Art. 17), restriction (Art. 18) and objection (Art. 21) —
plus storage limitation (Art. 5(1)(e)) and consent proof (Art. 7). It aggregates
personal data across pluggable providers so each subsystem owns its own data
while the framework orchestrates the request. Opt-in via `PRIVACY_ENABLED`.

## Model

A *subject* is an opaque `subject_id`; each provider decides how that maps to its
records (a user id, a conversation id, a tenant id, …) — the framework makes no
assumption about a single global identity scheme.

- **`DataProvider`** — a Protocol every personal-data store implements:
  `export(subject_id)` and `erase(subject_id) -> count`.
- **`RetentionProvider`** — an optional extension adding
  `purge_expired(older_than_seconds) -> count`.
- **`RectificationProvider`** — optional; `rectify(subject_id, corrections)`
  applies Art. 16 corrections and returns how many records changed.
- **`RestrictionProvider`** — optional; `restrict(subject_id, restricted)`
  flags Art. 18 restriction. Restriction is *not* erasure: the data stays, but
  providers honouring the flag must stop processing beyond storage.

Rectification and restriction are checked at runtime, so an existing provider
keeps working unchanged and simply reports as **unsupported** for those rights.
That is deliberate: Art. 19 obliges the controller to communicate a
rectification to each recipient, which it cannot do for a store it does not know
failed. Unsupported providers are named in the report, never silently skipped.

- **`DataSubjectService`** — aggregates all registered providers and emits an
  audit log line (`AUDIT | PRIVACY | …`) per operation. One failing provider is
  recorded and **does not abort** the others.

## Registering a provider

Each subsystem registers its provider at startup:

```python
from core.privacy import register_data_provider, DictDataProvider

provider = DictDataProvider("feedback")   # or a real store-backed provider
register_data_provider(provider)
```

## Built-in providers

When `PRIVACY_ENABLED` is set **and** PostgreSQL is enabled, the `api-routers`
plugin auto-registers `PostgresDataProvider` (name `postgres`) at startup — so
export/erasure/retention touch the relational store out of the box, no manual
wiring needed.

- **Subject mapping** — the `subject_id` is matched against
  `interactions.user_id`; export/erasure cover a subject's interactions and the
  feedback attached to them (children deleted first, FK-safe).
- **Tenant-scoped** — every query is bound to the active tenant
  (`get_tenant_or_default()`), so one tenant's admin can never reach another
  tenant's rows.
- **Retention** — sweeps purge expired `interactions`/`feedback` plus
  `chat_feedback` across **all tenants** (storage-limitation is a global
  data-lifecycle policy; only subject export/erasure are tenant-scoped).
  `chat_feedback` is conversation-keyed (no `user_id`), so it participates in
  retention only — not subject export/erasure.

## Retention enforcement (Art. 5(1)(e))

Retention is not just available on demand — it is **enforced** by a background
sweep when `PRIVACY_RETENTION_DAYS > 0` (and `PRIVACY_ENABLED`). The lifespan
starts a `RetentionScheduler` that runs `purge_expired(retention_days)` once
shortly after startup, then daily; sweep failures are logged and never kill the
loop. With `PRIVACY_RETENTION_DAYS=0` (the default) nothing runs — retention is
opt-in. Deployments preferring external orchestration can instead leave the
scheduler off and drive `POST /privacy/retention/sweep` from a cron job.

## Operations

```python
from core.privacy import get_data_subject_service

svc = get_data_subject_service()

bundle = await svc.export_subject("subject-123")   # Art. 15/20 access, portability
report = await svc.erase_subject("subject-123")    # Art. 17 erasure
sweep  = await svc.purge_expired(30 * 86400)       # Art. 5(1)(e) retention

# Art. 16 — correct inaccurate data across providers.
fixed = await svc.rectify_subject("subject-123", {"email": "new@example.test"})
print(fixed.unsupported)     # providers that hold data but cannot rectify it

# Art. 18 — restrict (or release) processing without erasing.
await svc.restrict_subject("subject-123")
await svc.restrict_subject("subject-123", restricted=False)

# Art. 21 — record an objection and apply its outcome.
objection = await svc.record_objection("subject-123", processing="profiling")
print(objection.outcome)     # ObjectionOutcome.UPHELD -> processing restricted
```

### Objections have two regimes

Art. 21(1) lets the controller continue if it demonstrates **compelling
legitimate grounds** overriding the subject's interests — pass
`override_grounds=` and the objection is recorded as `OVERRIDDEN` with those
grounds on the record. Art. 21(2)/(3) admits no such override for **direct
marketing**: `direct_marketing=True` is always upheld, any grounds passed are
discarded, and the attempt is logged.

## Consent (Art. 7)

Where consent is the lawful basis, Art. 7(1) puts the burden of proof on the
controller. A boolean column set at signup proves nothing; a record of *what*
was consented to, *when*, against *which notice version*, captured *how*, does.

```python
from core.privacy import get_consent_service

consent = get_consent_service()
await consent.grant("subject-123", "marketing", notice_version="v3",
                    evidence="signup-form")
await consent.has_consent("subject-123", "marketing")     # True
await consent.withdraw("subject-123", "marketing")        # Art. 7(3)
await consent.active_purposes("subject-123")              # []
```

Withdrawal **appends** rather than deletes: it operates for the future and must
not call into question the lawfulness of processing that already happened
(Art. 7(3)). `history()` returns the full chain — the Art. 7(1) proof. The
latest record for a purpose wins, so a re-grant restores consent without
rewriting history.

Set `PRIVACY_CONSENT_DB_PATH` in production. The default in-memory store
disappears on restart, and Art. 7(1) asks the controller to demonstrate consent
*later*, when challenged — a proof that does not survive a deploy is not proof.
The durable store orders records by sequence rather than timestamp, so a grant
and a withdrawal recorded inside the same clock tick still read back in the
order they happened; that pair is exactly the one whose order decides whether
consent is currently in force.

Consent records are themselves personal data, so `ConsentService` implements the
`DataProvider` protocol and can be registered alongside every other store:

```python
from core.privacy import register_data_provider

register_data_provider(get_consent_service())
```

## Automated decisions (Art. 22)

Art. 22(1) is a **prohibition with three exceptions**, not a disclosure duty: a
data subject has the right not to be subject to a solely automated decision
producing legal or similarly significant effects, unless it is necessary for a
contract, authorised by law, or based on explicit consent. On the contract and
consent grounds Art. 22(3) then requires at minimum the right to obtain **human
intervention**, to **express a point of view**, and to **contest** the decision.

```python
from core.privacy import (
    Art22Ground, AutomatedDecisionActivity, get_automated_decision_registry,
)

get_automated_decision_registry().register(
    AutomatedDecisionActivity(
        name="credit pre-screening",
        ground=Art22Ground.CONTRACT,
        human_intervention_channel="Reply to the decision email; an underwriter reviews.",
        express_view_channel="Free-text field on the appeal form.",
        contest_channel="Appeal form, 10 working-day SLA.",
        logic_explanation="Weighted score over income, obligations and history.",
        significance_and_consequences="A negative score delays the application "
                                      "pending manual review; it never rejects alone.",
    )
)
```

Both conditions must hold for the article to bite: `solely_automated` **and**
`legal_or_significant_effect`. Set either to false — because a competent human
actually decides, or because the effect is trivial — and the activity drops out
of scope, though Art. 13/14 transparency may still apply.

This is a **declaration, not a detector**. Whether a decision "significantly
affects" someone is a legal judgement about consequences, not a runtime
property. What the record buys is that the judgement was made, written down, and
can be audited: `registry.non_compliant()` lists in-scope activities whose
safeguards are missing, and `subject_information()` renders the Art. 15(1)(h)
disclosure to hand a data subject on request.

Set `PRIVACY_AUTOMATED_DECISIONS_DB_PATH` in production, for the same reason as
the consent log: this record *is* the evidence that the Art. 22(3) safeguards
exist and where a data subject reaches them — and unlike consent, that question
is usually asked months later, by a supervisory authority.

The legal-authorisation ground is treated differently on purpose: its safeguards
come from the authorising law, which this module cannot verify, so it records
the ground rather than demanding its own three channels.

## Admin API

When `PRIVACY_ENABLED` is set, the `api-routers` plugin mounts an admin DSR API
at `/privacy`, gated by the `privacy:manage`
[capability scope](auth.md#capability-scopes-fine-grained-authorization):

| Method & path                 | Purpose                              |
| ----------------------------- | ------------------------------------ |
| `GET /privacy/providers`      | List registered data providers       |
| `POST /privacy/export`        | Export all data for a subject        |
| `POST /privacy/erase`         | Erase all data for a subject         |
| `POST /privacy/retention/sweep` | Purge records older than N days    |

Every request is audit-logged with the subject id and affected record counts.

## Configuration

| Variable                 | Default | Description                              |
| ------------------------ | ------- | ---------------------------------------- |
| `PRIVACY_ENABLED`        | `false` | Enable the DSR subsystem and admin API   |
| `PRIVACY_RETENTION_DAYS` | `0`     | Retention horizon in days; `>0` starts the background sweep (0 = no auto-purge) |

!!! note "Wiring real stores"
    The relational store is wired by default via `PostgresDataProvider` (see
    [Built-in providers](#built-in-providers)). `DictDataProvider` (in-memory)
    remains the reference implementation used in tests. Other stores — vector
    memory (Qdrant), cache (Redis), the memory hierarchy — register against the
    same Protocol; their subject-identity mapping is a per-store decision and is
    not yet wired.
