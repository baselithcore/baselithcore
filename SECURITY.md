# Security Policy

BaselithCore is a modular orchestration engine for production-grade agentic AI.
It is deployed as critical infrastructure, so we take vulnerability reports
seriously and operate a coordinated disclosure process aligned with the
EU NIS2 Directive (2022/2555) Art. 21(2)(e) — vulnerability handling and
disclosure.

## Supported Versions

Security fixes land in the most recent minor release. Older minors do not
receive backports — upgrade to the latest release.

| Version | Supported          |
| ------- | ------------------ |
| 0.15.x  | :white_check_mark: |
| < 0.15  | :x:                |

The exact version is recorded in [`core/_version.py`](core/_version.py).

## Reporting a Vulnerability

**Do not open a public GitHub issue for security reports.** Public disclosure
before a fix ships puts every operator at risk.

Report privately via either channel:

- **GitHub Security Advisory** — use *Security → Report a vulnerability* on the
  repository (preferred; gives us a private collaboration thread).
- **Email** — `security@baselithcore.dev` (PGP key available on request).

Please include:

1. A reproducer (script, `curl`, or minimal repo).
2. Affected version (`core/_version.py`) and deployment surface (core API,
   a specific plugin, MCP/A2A, etc.).
3. Expected vs. observed behavior and the security impact.
4. A suggested severity (CVSS v3.1 vector if known).

We support encrypted reports; ask for the PGP key in your first message.

## Response Targets (SLA)

| Stage                       | Target               |
| --------------------------- | -------------------- |
| Acknowledgement             | 48 hours             |
| Triage + severity (CVSS)    | 5 business days      |
| Fix or mitigation plan      | severity-dependent\* |
| Coordinated public advisory | after a patch ships  |

\* Critical (CVSS ≥ 9.0): patch or mitigation targeted within 7 days.
High (7.0–8.9): within 30 days. Medium/Low: next scheduled release.

For operators subject to NIS2 incident-reporting obligations (early warning
within 24h, notification within 72h), treat a confirmed exploit against your
deployment as a reportable incident on your side — our SLA above governs the
upstream fix, not your regulatory clock.

## Disclosure Policy

We follow coordinated disclosure. Once a fix is released we publish a GitHub
Security Advisory (with a CVE where applicable) crediting the reporter unless
anonymity is requested. We ask reporters to hold public details until the
advisory is live.

## Security Architecture

The framework ships defense-in-depth controls that operators must configure
for their threat model. See the hardening guide at
[`mkdocs-site/docs/advanced/security.md`](mkdocs-site/docs/advanced/security.md)
and the supply-chain posture (SBOM, Trivy, Semgrep, cosign keyless signing,
SLSA provenance, signed plugins) documented there.

Operator hardening checklist:

- [ ] `SECRET_KEY` set to a high-entropy value (≥ 32 chars); never the default.
- [ ] `AUTH_REQUIRED=true` in any non-local deployment.
- [ ] TLS terminated in front of the app — the framework does not serve TLS.
- [ ] `MFA_ENABLED=true` and a TOTP step-up enforced for privileged accounts
      (NIS2 Art. 21(2)(j)); see
      [`core-modules/mfa.md`](mkdocs-site/docs/core-modules/mfa.md).
- [ ] Encryption at rest configured (`DATA_ENCRYPTION_KEYS`) and keys rotated.
- [ ] Least-privilege scoped API keys (`API_KEYS_SCOPED`) instead of broad roles.
- [ ] `BASELITH_REQUIRE_SIGNED_PLUGINS=true` to reject unsigned plugins.
- [ ] CORS (`ALLOW_ORIGINS`) is an explicit allowlist, never `*` with credentials.
- [ ] Observability wired (structlog + OpenTelemetry + Prometheus + Sentry DSN)
      so incidents are detected and the 24h/72h reporting clock can start.
- [ ] Backups verified (`scripts/verify-backup.sh --drill`) and a DR runbook
      rehearsed (RTO ≤ 1h / RPO ≤ 24h).

## Scope

In scope: the `core/` framework and the official plugins under `plugins/`.

Out of scope:

- Misconfiguration of an operator's own deployment (weak `SECRET_KEY`, exposed
  admin console, missing TLS terminator).
- Third-party MCP servers, A2A peers, or LLM providers the deployment connects
  to — audit those separately.
- Vulnerabilities in dependencies already tracked upstream (report to the
  dependency; we will bump on patch).
