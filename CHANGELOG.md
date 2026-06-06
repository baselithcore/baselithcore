# Changelog

All notable changes to this project are documented here. This file is
maintained automatically by semantic-release from Conventional Commits and
follows [Keep a Changelog](https://keepachangelog.com) and
[Semantic Versioning](https://semver.org).

## [Unreleased]

### Added

- **Encryption at rest** — versioned AES-256-GCM field encryption
  (`core.security.encryption`) with key rotation; opt-in via `DATA_ENCRYPTION_KEYS`.
- **Pluggable secret resolution** (`core.security.secrets`) — env / file
  (Docker & Kubernetes secrets) backends plus a registration hook for Vault/KMS.
- **Distributed lock** (`core.resilience.DistributedLock`) — Redis-backed mutex
  for multi-replica coordination (prevents cron/scheduler double-fire).
- **Dead-letter queue** (`core.task_queue.dead_letter`) — durable capture +
  replay of terminally-failed jobs, with admin endpoints under `/admin/dlq`.
- **Standardized error envelope** for unhandled and framework errors, with a
  correlation id (additive; `HTTPException`/validation responses unchanged).
- **API versioning** — additive `/v1` aliases (toggle `API_V1_ENABLED`).
- **Feature flags** (`core.feature_flags`) — runtime toggles, percentage
  rollout, kill-switches, pluggable backend.
- **Kubernetes Helm chart** and **Terraform** module under `deploy/`, including
  a scheduled backup CronJob and `/health/ready` readiness probe.
- **Backup verification** (`scripts/verify-backup.sh`) with integrity and
  restore-drill modes; gzip-aware restore.
- **SLOs & error-budget alerts** (`deploy/prometheus/slo-rules.yml`).
- **Supply chain**: CycloneDX SBOM and Trivy scan jobs in CI.

### Changed

- Raised the project test-coverage gate.

### Fixed

- `scripts/restore-db.sh` now restores gzipped (`.sql.gz`) backups.

---

> Earlier releases were published as GitHub Releases only. From the next release
> onward, version sections are appended above automatically.
