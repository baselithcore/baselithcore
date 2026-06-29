---
title: Privacy & Data-Subject Requests
description: GDPR access, portability, erasure, and retention across data providers
---

The `core/privacy` module is a **data-subject-request (DSR) framework** for GDPR
rights — access, portability, erasure, and retention. It aggregates personal
data across pluggable providers so each subsystem owns its own data while the
framework orchestrates the request. Opt-in via `PRIVACY_ENABLED`.

## Model

A *subject* is an opaque `subject_id`; each provider decides how that maps to its
records (a user id, a conversation id, a tenant id, …) — the framework makes no
assumption about a single global identity scheme.

- **`DataProvider`** — a Protocol every personal-data store implements:
  `export(subject_id)` and `erase(subject_id) -> count`.
- **`RetentionProvider`** — an optional extension adding
  `purge_expired(older_than_seconds) -> count`.
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

bundle = await svc.export_subject("subject-123")   # right to access/portability
report = await svc.erase_subject("subject-123")    # right to erasure
sweep  = await svc.purge_expired(30 * 86400)        # retention: drop >30d-old data
```

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
