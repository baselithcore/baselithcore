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
| `PRIVACY_RETENTION_DAYS` | `0`     | Default retention horizon (0 = no auto-purge) |

!!! note "Wiring real stores"
    The framework ships with `DictDataProvider` (in-memory, used as the
    reference implementation and in tests). Production providers — feedback,
    memory, conversation history — register against the same Protocol; their
    subject-identity mapping is a per-store decision.
