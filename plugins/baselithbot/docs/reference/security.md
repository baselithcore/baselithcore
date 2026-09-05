# Security & RBAC

BaselithBot is **not** wired to the central `auth` plugin's RBAC on the
backend — grep confirms zero imports of `plugins.auth.dependencies`
anywhere in its Python source, and no route uses `require_roles` /
`require_permission` / `require_tab`. Instead the backend guards itself
with its own static bearer token, and the framework `core.auth` seam is
consulted only *optionally* and *read-only*, to resolve a tenant id for the
replay history (see [Multi-tenancy](tenancy.md)). This is a deliberate
hybrid, not an oversight.

## 1. Dashboard bearer token

Every dashboard *write* endpoint and `POST /baselithbot/run` runs through
`DashboardAuth` (`policies/dashboard_auth.py`).

**Configure:**

```bash
export BASELITHBOT_DASHBOARD_TOKEN=$(openssl rand -hex 32)
```

**Present:** `Authorization: Bearer <token>`, or `?token=<token>` as a
fallback for contexts (like SSE) where setting a header is impractical.
Comparison is `hmac.compare_digest` — timing-safe.

**Dev mode:** if the token is unset, reads and writes both pass, with a
one-shot warning logged (`baselithbot_dashboard_open`). Not safe for a
shared network.

This token is **entirely separate** from the central `auth` plugin's access
tokens — see the tenancy note below for what that means in practice.

## 2. Rate limits

Per-client token-bucket limiters, keyed `"<route-prefix>:<client-ip>"`.

| Route | Window | Max |
|---|---|---|
| `POST /run` | 60s | 10 |
| `WS /ws/pair` | 60s | 20 |
| `POST /dash/sessions` / `.../send` | 60s | 30 |
| `DELETE /dash/sessions/{sid}` | 60s | 20 |
| `POST /dash/crons/{name}/remove` | 60s | 20 |
| `POST /dash/nodes/token` | 60s | 5 |
| `DELETE /dash/nodes/{node_id}` | 60s | 20 |
| `PUT /dash/models` | 60s | 5 |
| `POST /dash/approvals/{id}/approve\|deny` | 60s | 5 |

A reverse proxy must set `X-Forwarded-For` and FastAPI must trust it via
`ProxyHeadersMiddleware` for the limiter to key on the real client — see
the [runbook](../operations/runbook.md). Over-limit response:
`HTTPException(429, "rate limit exceeded")`.

## 3. Inbound hardening

- Body cap 1 MiB (`413` on overflow); malformed JSON tolerated as
  `{"raw": "<decoded-utf8>"}`.
- `DMPairingPolicy.evaluate()` denies DMs from unpaired senders.
- Every accepted event logged with secrets redacted.
- An optional host allowlist is available (`policies/host_acl.py`).

## 4. Static UI hardening

Every response from the UI mount carries `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and
`Permissions-Policy: microphone=(), camera=(), geolocation=()`. No CSP yet.

## 5. Node pairing

Tokens are single-use and short-lived; revoke via `DELETE
/dash/nodes/{node_id}`. WS handshake rate-limited 20/min per client IP; a
bad handshake closes `4000`, exceeding the rate limit closes `4290`. See
[Nodes](../guide/nodes.md).

## 6. Computer Use

Summary — full detail in [Computer Use](../guide/computer-use.md) and
[Configuration](configuration.md):

- Default **off** (`enabled=false`); every sub-capability gates
  independently.
- Shell allowlist + `shell=False` (argv vector, never a string) + timeout.
- Filesystem scoping via `Path.resolve()` + `relative_to(root)` — `..`
  traversal and cross-root symlinks blocked — plus a byte cap.
- JSON-Lines audit log with secret redaction, batched flush.
- Human-in-the-loop approval gate via `require_approval_for` — see
  [Approvals](../guide/approvals.md).
- Dashboard edits to `computer_use`/`stealth` persist to a runtime overlay
  and invalidate the cached agent.

## 7. Encrypted secrets at rest

- **Provider API keys** — `security/secret_store.py` persists operator
  keys in `<state>/provider_keys.enc.json` encrypted with Fernet. Master
  key from `BASELITHBOT_SECRET_KEY`, or auto-generated once under
  `<state>/.secret_key` (mode `0600`). Reads surface only `***<last4>`
  previews — plaintext is never returned by the API.
- **Replay screenshots** — optionally encrypted with Fernet when
  `BASELITHBOT_REPLAY_ENCRYPTION_KEY` is set; ciphertext without an
  available key is refused rather than served. See
  [Replay](../guide/replay.md).

State files (`plugins/baselithbot/.state/`) are excluded from git.

## 8. Secret redaction

`security/redaction.py` scrubs keys matching `token`, `password`, `secret`,
`api_key`, `webhook_url`, `authorization`, `cookie`, `private_key`
(case-insensitive) from both structured logs and audit entries — values
become `"***redacted***"`.

## 9. The dashboard UI and the API guard

The React dashboard is served by the plugin itself (`api/router.py`, see
[Static UI hardening](#4-static-ui-hardening)) and carries no login
wrapper of its own: reaching the SPA shell is not gated, acting through
its API is. API calls from the dashboard authenticate with the plugin's own
`BASELITHBOT_DASHBOARD_TOKEN`, stored client-side in `sessionStorage`
(`baselithbot.dashboard.token`) or passed as `?token=`. A deployment that
wants the shell itself behind a login places the plugin behind its
reverse proxy or its own auth layer; the dashboard token stays required
either way, and rotates independently of any such session.

## 10. Threat model notes

| Threat | Mitigation |
|---|---|
| LLM prompt-injection → arbitrary shell | `allowed_shell_commands` + `shell=False` + audit |
| LLM prompt-injection → filesystem escape | `ScopedFileSystem` + `relative_to(root)` |
| Stolen dashboard token | Rotate env var → process restart; constant-time compare prevents timing leak |
| Open dashboard on a public IP in dev mode | Startup warning + banner in UI |
| Overloading `/run` | Rate limit + singleton agent with bounded `max_steps` |
| Malicious inbound payload | 1 MiB cap + parser + DM policy + host ACL |
| Exfiltration through canvas/voice | Secrets redacted before render; A2UI output is server-owned |

## 11. Operational checklist

- [ ] `BASELITHBOT_DASHBOARD_TOKEN` set in production
- [ ] Reverse proxy forwards `X-Forwarded-For` for the rate limiter
- [ ] Audit log volume append-only
- [ ] `allow_shell` / `allow_filesystem` disabled unless explicitly required
- [ ] `require_approval_for` populated for privileged capabilities in shared environments
- [ ] Paired nodes reviewed periodically (`GET /dash/nodes`)
- [ ] `plugins/baselithbot/.state/` never committed
- [ ] `BASELITHBOT_SECRET_KEY` rotated if `provider_keys.enc.json` leaks
- [ ] `baselithbot_tool_errors_total{tool="shell_run"}` alert rule configured
