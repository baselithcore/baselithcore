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
| `plugin.load` | `core/plugins/loader.py` — a plugin finished `initialize()` (`resource` is `plugin:<name>`, `details` carry its version and directory) |
| `plugin.unload` | `core/plugins/registry.py` — `unregister()` removed a plugin and ran its `shutdown()` |
| `privacy.export` / `privacy.erase` / `privacy.rectify` / `privacy.restrict` / `privacy.object` / `privacy.retention` | `core/privacy/service.py` — one event per data-subject request (`resource` is the subject id; `action` is `export`, `erase`, `rectify`, `restrict`/`release`, `object` or `retention_sweep`) |
| `privacy.consent` | `core/privacy/consent.py` — `action="grant"` on `ConsentService.grant`, `action="withdraw"` on `withdraw` (`details.purpose` names the processing purpose) |
| `transparency.mark` | `core/transparency/service.py` |
| `incident.open` / `incident.milestone` / `incident.close` | `core/incidents/service.py` |
| `tool.invoke` / `tool.blocked` | `core/orchestration/enforcement.py` — every tool invocation gated by `enforce_tool_invocation` |
| `self_modify.propose` | `core/skill_evolution/service.py` — a skill synthesis proposal enters the gate; `core/optimization/evolution/evolve.py` — an evolutionary mutation is accepted into the archive |
| `self_modify.apply` / `self_modify.reject` | `core/skill_evolution/gating.py` (skill gate decisions), `core/optimization/tune_gate.py` (auto-tune eval gate) and `core/optimization/compile.py` (prompt-compilation landing) |
| `self_modify.rollback` | `core/skill_evolution/service.py` — a skill passed the eval gate but the `self_modify` autonomy approval was refused; the write is rolled back and audited with `success=False` (`action="skill_evolution.approval_rollback"`, `details.reason`, `details.rolled_back`) |
| `compliance.register` | `core/compliance/registry.py` — `action="register"` when an AI system enters the register (`success=False` for an Art. 5 prohibited category), `action="lifecycle"` on `advance_lifecycle` |
| `compliance.assessment` | `core/compliance/prohibited.py` (`art5_screening`, `success=False` when prohibited), `core/compliance/registry.py` (`reclassify`), `core/compliance/artefact_services.py` (`risk_management`, `instructions_for_use`, `dpia`), `core/compliance/documents.py` (`annex_iv_documentation`, `fria`, `ropa_entry`), `core/compliance/post_market_service.py` (`post_market_plan`, `post_market_observation`), `core/compliance/review_sweep.py` (`compliance_review_sweep`) |
| `payment.executed` / `payment.failed` | `core/world_model/payments.py` — every `execute_payment` outcome: `executed` for a captured charge, `failed` for a decline or an executor error (`resource` is the merchant id, `action` the intent id; `details` carry `transaction_id`, `amount_cents`, `status`, `psp`). See [World Model — AP2 Payment Execution](world-model.md#ap2-payment-execution-execute_payment) |

Successful per-request authentication is deliberately **not** emitted as an
audit record — it is a per-request hot path, and the volume would drown the
security-relevant signal. It remains available as a log line.

### Tool invocations (`tool.invoke` / `tool.blocked`)

The orchestration enforcement chokepoint records **one event per gated tool
invocation**: `tool.invoke` when every gate passed, `tool.blocked` (with the
refusal reason in `details.reason`, `success=False`) when any gate raised.
`resource` is the tool name, `action` the autonomy category, and
`agent_id`/`tenant_id` ride along when the context carries them. Arguments
appear only as `details.args_digest` — a SHA-256 over their canonical JSON,
never the raw values, which may hold secrets or PII. Emission is best-effort:
an audit failure can never break the tool path.

### Self-modification (`self_modify.*`)

Any change the system makes to its **own future behavior** — skill
synthesis, automated prompt tuning — is audited under the `self_modify.*`
family: `propose` when a candidate enters review, `apply` when it is
accepted, `reject` when it is refused (a rejection that rolled the change
back carries `details.rolled_back`). `self_modify.rollback` records a
standalone rollback: emitted when an eval-accepted skill is rolled back
because the `self_modify` human-approval gate denied it (or no approval
channel was available). Skill-gate records include the
validation score, previous best, and (for multi-objective validators) the
fitness breakdown; tune-gate records include the score and the registered
candidate prompt version. See
[Skill Evolution](skill-evolution.md#governed-self-modification) and
[Optimization](optimization.md#eval-gate-on-auto-tune-baselith_optimizer_eval_gate).

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
