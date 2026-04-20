# Security Policy

Baselithbot runs with privileged capabilities — browser control, desktop
automation, shell execution, secret storage. Treat it accordingly.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | yes       |
| < 1.0   | no        |

Security fixes land in the most recent minor release. Downgrade paths are
not supported — upgrade instead.

## Threat Model

A threat model and hardening guide is maintained in
[`docs/security.md`](./docs/security.md). Operators MUST review it before
enabling any of:

- `POST /baselithbot/run` (remote task execution)
- Desktop lane (`DesktopAgent`, `desktop_lane.py`) — grants screen-read and
  input-inject capability.
- Shell executor (`shell_exec.py`) — sandboxed but destructive by design.
- Custom cron (`cron_custom.py`) — persistent user-defined jobs.
- Secret store (`secret_store.py`) — Fernet-encrypted provider credentials.

## Hardening Checklist

- [ ] `BASELITHBOT_DASHBOARD_TOKEN` set to a 256-bit secret.
- [ ] `BASELITHBOT_FERNET_KEY` rotated on first deploy; offline backup kept.
- [ ] Rate limits configured via `runtime_config.py`.
- [ ] `ApprovalGate` enabled for all destructive intents.
- [ ] Inbound dispatcher (`inbound/`) allowlist is explicit, not wildcard.
- [ ] `js_whitelist.py` reviewed; no unsafe sinks added.
- [ ] Observability wired: metrics + tracing + Sentry DSN for alerting.
- [ ] HTTPS terminator in front of the dashboard — the plugin does not
      serve TLS directly.

## Reporting a Vulnerability

Do **not** open a public issue for security reports.

Email: `baselith.ai@gmail.com`
PGP: available on request.

Include:

1. A reproducer (script, `curl`, or video).
2. Affected version (`plugins/baselithbot/manifest.yaml:version`).
3. Expected vs. observed behavior.
4. Suggested severity (CVSS v3.1 vector if known).

**Response targets:**

| Stage             | SLA               |
| ----------------- | ----------------- |
| Acknowledgement   | 48 hours          |
| Triage + severity | 5 business days   |
| Fix or mitigation | depends on CVSS   |
| Public disclosure | after patch ships |

## Secret Redaction

The plugin ships with `secret_redaction.py`, which scrubs known credential
patterns from logs and replay bundles. If a secret leaks to an outbound
channel, treat it as compromised and rotate immediately — redaction is
defense-in-depth, not a primary control.

## Out of Scope

- Browser fingerprinting detection (`stealth.py`) is a best-effort utility,
  not a guarantee. Do not rely on it for compliance.
- Desktop control on untrusted hosts. The host OS is the trust boundary.
- Third-party MCP servers the plugin talks to — audit them separately.
