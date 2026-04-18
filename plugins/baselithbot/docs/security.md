# Security

[← Index](./README.md)

Authentication, rate limits, inbound hardening, static UI headers, audit
log posture.

## 1. Bearer token

Every dashboard *write* endpoint and `POST /baselithbot/run` runs through
[`DashboardAuth`](../policies/dashboard_auth.py).

**Configure:**

```bash
export BASELITHBOT_DASHBOARD_TOKEN=$(openssl rand -hex 32)
```

**Present:**

```http
Authorization: Bearer <token>
```

or `?token=<token>` query param (fallback for SSE where headers hard to
set).

**Comparison:** `hmac.compare_digest` — timing-safe.

**Dev mode:** unset token → reads pass, writes pass **with a warning logged
once** (`baselithbot_dashboard_open`). Not safe for multi-tenant deploys.

## 2. Rate limits

Per-client token-bucket limiters ([`policies/rate_limit.py`](../policies/rate_limit.py)).

| Route | Window | Max |
|-------|--------|-----|
| `POST /baselithbot/run` | 60s | 10 |
| `WS /baselithbot/ws/pair` | 60s | 20 |
| `POST /dash/sessions` | 60s | 30 |
| `POST /dash/sessions/{sid}/send` | 60s | 30 |
| `DELETE /dash/sessions/{sid}` | 60s | 20 |
| `POST /dash/crons/{name}/remove` | 60s | 20 |
| `POST /dash/nodes/token` | 60s | 5 |
| `DELETE /dash/nodes/{node_id}` | 60s | 20 |
| `PUT /dash/models` | 60s | 5 |

Keying: `"<route-prefix>:<client-ip>"`. Reverse proxy must set
`X-Forwarded-For` and FastAPI must trust it via `ProxyHeadersMiddleware`
for the limiter to key on the real client.

Over-limit response: `HTTPException(429, "rate limit exceeded")`.

## 3. Inbound hardening

- Body cap: 1 MiB (`_MAX_INBOUND_BODY_BYTES`). 413 on overflow.
- Malformed JSON tolerated: becomes `{"raw": "<decoded-utf8>"}`.
- `DMPairingPolicy.evaluate()` gates DMs from unpaired senders — emits
  `{"status": "denied", "reason": ...}`.
- Redacted structured log on every accepted event.
- Host allowlist available via [`policies/host_acl.py`](../policies/host_acl.py).

## 4. Static UI hardening

Every response from the UI mount carries:

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `no-referrer` |
| `Permissions-Policy` | `microphone=(), camera=(), geolocation=()` |

No CSP yet — tracked for V1.1 (needs inline-style audit in Vite bundle).

## 5. Node pairing

- Tokens single-use and short-lived (internal implementation detail in
  [`nodes/pairing.py`](../nodes/pairing.py)).
- Revoke: `DELETE /dash/nodes/{node_id}` (🔒).
- WS handshake rate-limited 20/min per client IP.
- Bad handshake → WebSocket close `4000`; rate limit → close `4290`.

## 6. Computer Use

Summary only — full detail in [computer-use.md](./computer-use.md):

- Default **off** (`enabled=false`).
- Per-capability flags.
- Shell allowlist + `shell=False` + timeout.
- Filesystem scoping + byte cap + `..` blocked.
- JSON-Lines audit log with secret redaction.
- **Human-in-the-loop approval gate** via
  `ComputerUseConfig.require_approval_for`; privileged actions suspend
  until a dashboard operator approves/denies (timeout → auto-deny,
  audited). See [approvals.md](./approvals.md).
- Runtime overlay: dashboard edits to `computer_use`/`stealth` persist to
  `plugins/baselithbot/.state/runtime_config.json` (git-ignored) and
  invalidate the cached agent.

## 6bis. Encrypted provider keys at rest

[`secret_store.py`](../secret_store.py) persists operator-supplied
provider API keys in `<state>/provider_keys.enc.json` encrypted with
Fernet. Master key from `BASELITHBOT_SECRET_KEY` env, or auto-generated
once under `<state>/.secret_key` (mode `0600`). Plaintext is never
returned by the API — reads surface only `***<last4>` previews. State
files are excluded from git via `plugins/*/.state/` in `.gitignore`.

## 7. Secret redaction

[`secret_redaction.py`](../secret_redaction.py) scrubs keys matching
`token`, `password`, `secret`, `api_key`, `webhook_url`, `authorization`,
`cookie`, `private_key` (case-insensitive) from both structured logs and
audit entries. Values become `"***redacted***"`.

Apply manually via `redact_payload(dict)`; invoked automatically by
`AuditLogger.record` and inside most MCP tool error handlers.

## 8. Threat model notes

| Threat | Mitigation |
|--------|------------|
| LLM prompt-injection → arbitrary shell | `allowed_shell_commands` + `shell=False` + audit |
| LLM prompt-injection → filesystem escape | `ScopedFileSystem` + `relative_to(root)` |
| Stolen dashboard token | Rotate env var → process restart; constant-time compare prevents timing leak |
| Open dashboard on public IP in dev mode | Startup warning + banner in UI |
| Overloading `/run` endpoint | Rate limit + singleton agent with bounded max_steps |
| Malicious inbound payload | 1 MiB cap + parser + DM policy + host ACL |
| Exfiltration through canvas/voice | Secrets redacted before render; A2UI output is server-owned |

## 9. Operational checklist

- [ ] `BASELITHBOT_DASHBOARD_TOKEN` set in production env
- [ ] Reverse proxy forwards `X-Forwarded-For` for rate limiter
- [ ] Audit log volume append-only (e.g. EBS snapshot policy / immutable S3)
- [ ] `allow_shell` and `allow_filesystem` disabled unless explicitly required
- [ ] `require_approval_for` populated for privileged capabilities in shared environments
- [ ] Paired nodes reviewed periodically (`GET /dash/nodes`)
- [ ] Model prefs updates alerted on (`models.updated` SSE event)
- [ ] `plugins/baselithbot/.state/` never committed (covered by `.gitignore`)
- [ ] `BASELITHBOT_SECRET_KEY` rotated if `provider_keys.enc.json` leaks
- [ ] `baselithbot_tool_errors_total{tool="shell_run"}` alert rule configured
