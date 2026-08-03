---
title: Audit Trail
description: Durable, hash-chained audit records for AI Act Art. 12/19, NIS2 and GDPR accountability
---

The framework has always emitted `AUDIT | …` log lines. Those are *telemetry*:
they reach stdout, then whatever the operator's log pipeline decides. Several
regimes need something stronger — records that **survive**, stay **queryable**,
and can be shown to be **unaltered**:

| Obligation | What it demands |
| ---------- | --------------- |
| **EU AI Act Art. 12** | Automatic recording of events over the system's lifetime |
| **EU AI Act Art. 19** | The provider keeps those logs for **≥ 6 months** |
| **EU AI Act Art. 26(6)** | The deployer keeps them for **≥ 6 months** too |
| **NIS2 Art. 21(2)(b)** | An evidence trail behind each incident filing |
| **GDPR Art. 5(2)** | Accountability — being able to *demonstrate* compliance |

`core/observability/audit*.py` turns the existing log lines into retained
records. It is **opt-in** (`AUDIT_ENABLED`, default off) and **additive**: with
the flag unset the historical logger-only behaviour is byte-for-byte unchanged.

## Layout

| Module | Role |
| ------ | ---- |
| [`audit.py`](https://github.com/baselithcore/baselithcore) | Event model (`AuditEvent`, `AuditEventType`), sink protocol, fan-out `AuditLogger`, `audit_emit()` |
| `audit_chain.py` | `SQLiteAuditSink` — append-only, hash-chained, queryable, purgeable |
| `audit_setup.py` | Builds the logger from config; owns the retention sweep |
| `core/config/audit.py` | `AuditConfig` / `get_audit_config()` |

## Configuration

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `AUDIT_ENABLED` | `false` | Master switch for the durable trail |
| `AUDIT_LOG_SINK_ENABLED` | `true` | Keep mirroring events to the structured logger |
| `AUDIT_FILE_PATH` | `None` | Append events as JSON lines to this file |
| `AUDIT_DB_PATH` | `None` | SQLite path for the durable, hash-chained sink |
| `AUDIT_HASH_CHAIN` | `true` | Link each record to its predecessor (tamper evidence) |
| `AUDIT_RETENTION_DAYS` | `180` | Retention horizon; `0` keeps records forever |
| `AUDIT_MAX_DETAIL_CHARS` | `2000` | Cap on caller-supplied `details` per record |

The 180-day default *is* the AI Act Art. 19 / Art. 26(6) six-month floor.
Configuring less is allowed (not every deployment is in AI Act scope) but is
warned about at startup:

```text
AUDIT_RETENTION_DAYS=30 is below the EU AI Act Art. 19/26(6) six-month floor
(180 days); logs will be purged before the statutory minimum.
```

## What gets recorded

Wiring is in place at the compliance-relevant call sites — each keeps its
original log line *and* emits a structured event:

| Event type | Source |
| ---------- | ------ |
| `auth.failed` | `core/middleware/security.py` — unauthorized / forbidden |
| `privacy.export` / `privacy.erase` / `privacy.retention` | `core/privacy/service.py` |
| `transparency.mark` | `core/transparency/service.py` |
| `incident.open` / `incident.milestone` / `incident.close` | `core/incidents/service.py` |

Successful per-request authentication is deliberately **not** emitted as an
audit record — it is a per-request hot path, and the volume would drown the
security-relevant signal. It remains available as a log line.

## Recording an event

From async code:

```python
from core.observability.audit import AuditEventType, get_audit_logger

await get_audit_logger().log(
    AuditEventType.ADMIN_ACTION,
    user_id="alice",
    resource="/admin/config",
    action="update",
    details={"key": "LLM_MODEL"},
)
```

From synchronous code, `audit_emit()` is fire-and-forget — audit recording must
never change the control flow of the code it observes:

```python
from core.observability.audit import AuditEventType, audit_emit

audit_emit(AuditEventType.CONFIG_CHANGE, action="reload")
```

With a running loop the write is scheduled as a task (a strong reference is held
until it completes, so it cannot be collected mid-flight). Without one — module
import, a worker thread, a sync CLI path — the event degrades to the logger
representation rather than being dropped.

## Tamper evidence

Every record stores `prev_hash` (its predecessor's `entry_hash`) and

```text
entry_hash = SHA-256(prev_hash ‖ canonical_json(event))
```

Editing or deleting a record breaks every downstream link:

```python
from core.observability.audit_setup import get_durable_audit_sink

result = get_durable_audit_sink().verify_chain()
if not result.ok:
    alert(f"audit chain broken at seq={result.broken_at}: {result.reason}")
```

This is **detection, not prevention** — an attacker with write access to the
file can rebuild the whole chain. When the threat model includes a compromised
host, anchor the digest externally: ship `sink.head_hash()` periodically to a
WORM store or a separate SIEM, and compare on verification.

## Retention and truncation

A purge legitimately removes the chain's oldest links. Verification therefore
takes the earliest **surviving** record's `prev_hash` as a trusted anchor and
validates forward, so a retention sweep is never reported as tampering.

The sweep runs daily and is started from the lifespan when `AUDIT_ENABLED`,
`AUDIT_DB_PATH` and `AUDIT_RETENTION_DAYS > 0` are all set. Deployments that
prefer external orchestration can leave `AUDIT_RETENTION_DAYS=0` and drive
`purge_older_than()` from a cron job.

## Querying

```python
from core.observability.audit_chain import AuditQuery
from core.observability.audit_setup import get_durable_audit_sink

sink = get_durable_audit_sink()
rows = sink.query(AuditQuery(event_type="privacy.erase", tenant_id="acme", limit=50))
```

Filters compose over event type, user, tenant, and a time window; results come
back newest-first with `details` already decoded.

## Design notes

- **Storage is stdlib `sqlite3`**, matching `core/incidents/persistence.py`:
  zero new dependencies, no infrastructure, single-writer semantics that suit an
  append-only workload. Writes are offloaded to the default executor so a
  disk-bound append never blocks the request path.
- **Sink failures are contained.** One broken sink never breaks the request path
  and never stops the remaining sinks from recording the event.
- **Details are bounded.** Oversized `details` are truncated *before* hashing,
  so the digest always covers exactly the bytes that were stored.
