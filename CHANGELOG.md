# Changelog

All notable changes to this project are documented here. This file is
maintained automatically by semantic-release from Conventional Commits and
follows [Keep a Changelog](https://keepachangelog.com) and
[Semantic Versioning](https://semver.org).

# [0.14.0](https://github.com/baselithcore/baselithcore/compare/v0.13.0...v0.14.0) (2026-06-17)


### Bug Fixes

* verify API route presence via OpenAPI schema instead of app.routes to support FastAPI 0.137+ lazy resolution ([56a653a](https://github.com/baselithcore/baselithcore/commit/56a653ab4bee2a7828484a55d6962ca7bb71276c))


### Features

* add chaos resilience tests and Locust load testing suite ([0ac5e49](https://github.com/baselithcore/baselithcore/commit/0ac5e496cfdf00ab9d088b91027af1963b7d3c8e))
* add persistent request quota enforcement and generic cursor-based pagination utilities ([d307f17](https://github.com/baselithcore/baselithcore/commit/d307f17ca812abca5fbd4fe32c50eba369393462))
* bypass gzip compression for Server-Sent Events to prevent stream buffering ([b127512](https://github.com/baselithcore/baselithcore/commit/b127512189fb1a880a8a54ba9a1b382c39fb4b45))
* implement automated supply-chain security with Dependabot, CodeQL, and Semgrep scanning ([d50ac56](https://github.com/baselithcore/baselithcore/commit/d50ac5680dd4dbbe688ab121fd8aa61b991231d2))
* implement Baselith Python SDK with client, models, and error handling ([2811cd1](https://github.com/baselithcore/baselithcore/commit/2811cd16ac18cf95176135c927dda2a14d1b1b12))
* implement bounded LRU cache for JWT verification and secure LLM provider API keys using SecretStr. ([875dd26](https://github.com/baselithcore/baselithcore/commit/875dd262c20a8b03cc19ed82bbdbf5a4ba0abe85))
* implement extensive performance optimizations for 0.14 including connection pooling, request-path caching, concurrent execution, and query bounds. ([e138e37](https://github.com/baselithcore/baselithcore/commit/e138e374896da058f1c4c9bf0e8ecbbf5b4334c9))
* implement fine-grained capability scopes and federated OIDC identity provider integration ([8481edf](https://github.com/baselithcore/baselithcore/commit/8481edf4dbf9a3d2fb1d9a70fc73e867c4196305))
* implement outbound webhook subsystem with configurable delivery, SSRF protection, and event dispatching ([a1e0fca](https://github.com/baselithcore/baselithcore/commit/a1e0fca693a4702e22f0f6d8c6c0233e079478a1))
* implement privacy and data-subject request (DSR) framework for GDPR compliance ([7c71c71](https://github.com/baselithcore/baselithcore/commit/7c71c710f8f07388e5c18bcf88559cc4af1254c2))
* implement tenant isolation guards, per-tenant encryption, and add performance microbenchmarks ([bf078c2](https://github.com/baselithcore/baselithcore/commit/bf078c2f4cc2b095f763612ffb2da549d8609484))
* implement versioned prompt registry with YAML-based file loading, template rendering, and tracing support ([8374e31](https://github.com/baselithcore/baselithcore/commit/8374e315fc0293fcc9a113d8c680c8b49e3c5d50))


### Performance Improvements

* implement experience replay episode capping and JWT verification caching to improve system efficiency ([164b8b6](https://github.com/baselithcore/baselithcore/commit/164b8b6290117eff6857e1f8b9889703abb773aa))

# [0.13.0](https://github.com/baselithcore/baselithcore/compare/v0.12.0...v0.13.0) (2026-06-11)


### Features

* configure .env file loading for service settings and update API key aliases to support provider-specific prefixes ([af0aec2](https://github.com/baselithcore/baselithcore/commit/af0aec271cd854006b39f9ec440fddbbafaaa7ef))
* harden MCP security with process command allowlists and autonomy-based tool execution gates ([e48f6a8](https://github.com/baselithcore/baselithcore/commit/e48f6a84fb9b034490e15a736202165eb76e4623))
* implement single-load environment parsing for performance and add tool autonomy approval gating logic ([129765f](https://github.com/baselithcore/baselithcore/commit/129765f1fbac669e853ba9928c8ccfae98512b8c))


### Performance Improvements

* implement vectorized semantic cache scans, eager auth singleton initialization, and deterministic cache key serialization ([386eab4](https://github.com/baselithcore/baselithcore/commit/386eab46cb221a0b8986a8c8e6cdbdf2fb61f11e))
* optimize performance across services by streamlining Redis rate limiting, offloading blocking operations to threads, and improving token counting efficiency. ([7fc3fcf](https://github.com/baselithcore/baselithcore/commit/7fc3fcf57e66e8c043c34365f786e206326c53cb))

# [0.12.0](https://github.com/baselithcore/baselithcore/compare/v0.11.1...v0.12.0) (2026-06-07)


### Bug Fixes

* correct cyclonedx-py flag and migrate trivy-action to manual CLI installation ([e6bdf3d](https://github.com/baselithcore/baselithcore/commit/e6bdf3d7e8c46b4d1fd81d27c0d269f632981f1b))
* normalize plugin naming conventions, improve lifecycle metadata handling, and secure database credential extraction ([0363c4f](https://github.com/baselithcore/baselithcore/commit/0363c4f778eac174490f5808b3efe513a3f54054))
* update Trivy installation to use direct tarball download and ignore non-zero scan exit codes ([d5bff4d](https://github.com/baselithcore/baselithcore/commit/d5bff4d357907754fda2d2a4124316f8a37e7c19))


### Features

* centralize OpenTelemetry initialization and tracing bridge in new core module ([74f8593](https://github.com/baselithcore/baselithcore/commit/74f8593fac9edbd2b5f81f2be8987a896396d8fd))
* implement dependency-free static admin console and add path-scoped CSP relaxation for docs routes ([16a8e85](https://github.com/baselithcore/baselithcore/commit/16a8e85b320a17b12ffbf326d8a61d3149f98f1b))
* implement distributed locking, secure secret management, and full Kubernetes deployment infrastructure. ([73e9cfe](https://github.com/baselithcore/baselithcore/commit/73e9cfedcda917afa5c383e8d2a620422f2100f1))
* implement durable dead-letter queue for background job recovery and persistence ([ff7184d](https://github.com/baselithcore/baselithcore/commit/ff7184da4cb87229c990157fab2cdbf085a7f9ec))
* implement feature flags module, automate CHANGELOG generation, and add release image signing workflow. ([625bc2f](https://github.com/baselithcore/baselithcore/commit/625bc2f3134a6049447ba8572cdb902e7c8d7e03))

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
