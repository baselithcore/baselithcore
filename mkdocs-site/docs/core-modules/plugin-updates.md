---
title: Plugin Updates
description: Signed plugin release detection, the trust model and the admin API
---

The `core/plugin_updates` module watches GitHub releases and tells
administrators when a newer version exists: of the framework itself (the
system update notice, on by default) and of your plugins (only once a sources
file is configured).

What it contacts: `api.github.com` (or `PLUGIN_UPDATE_GITHUB_API_URL`), for the
releases and published security advisories of `SYSTEM_UPDATE_REPO`, about 60
seconds after boot and then every `PLUGIN_UPDATE_CHECK_INTERVAL_SECONDS`. Each
API worker process runs its own check. The requests are plain reads of release
data: nothing about the deployment (its version, plugins, hosts or users) is
sent beyond what any HTTPS request carries (the source address, the HTTP
client's user agent and, when configured, the token). Set `SYSTEM_UPDATE_REPO=""` to switch the system check off;
with no sources file either, the module never contacts GitHub.

!!! warning "Upgrade note"
    Earlier releases stayed silent until a sources file was configured. From
    this release on, a deployment with default settings contacts the GitHub API
    for the framework's releases and advisories. An air-gapped or egress-filtered
    deployment should set `SYSTEM_UPDATE_REPO=""` (or allow `api.github.com`),
    otherwise each check fails and the report carries the error.

!!! note "Detection only, for now"
    This release detects and reports updates. Installing an update is not part of
    this release; until then a new version is deployed the usual way.

## What it does

Every six hours (and on demand) the service reads the sources file, asks each
mirror repository for its latest release, downloads the release artifact and
runs the full verification described below. A release that passes is reported
as `available`; one that fails is reported with the reason it was refused. The
last report is cached on disk, and the `plugin.update_available` event is
emitted on the EventBus once per new version. When the cache directory is not
writable the failure is logged (`plugin_update_cache_write_failed`) and the
metric and events still go out.

## Trust model

Only signed releases are ever offered. The verifier reuses the plugin signing
machinery in `core/plugins/signing.py`: an Ed25519 signature over the
manifest's `integrity_sha256`, checked against the deployment's trust store
(with expiry and revocation). The digest covers the manifest itself, so the
signature attests `version`, `permissions`, `python_dependencies` and
`min_core_version` as well.

In order, before anything is offered:

1. The artifact comes from the release registered for that plugin, over HTTPS,
   by tag `v<version>`.
2. The recomputed plugin hash equals the manifest's `integrity_sha256`.
3. `signature_ed25519` verifies against a usable trusted key. Unsigned or
   unknown-key releases are refused regardless of
   `BASELITH_REQUIRE_PLUGIN_SIGNATURES`; the update path is always strict.
4. The manifest `name` is the plugin being updated and its `version` equals the
   tag.
5. No downgrade: the version must be strictly greater than the installed one.
6. The running core satisfies the plugin's `min_core_version` and
   `max_core_version`.
7. Every `python_dependencies` requirement is already satisfied in the running
   environment.

The signing key never leaves the maintainers, so a tag pushed by anyone else
fails rule 3.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PLUGIN_UPDATE_SOURCES_FILE` | unset | YAML file in the `mirrors: {name: {repo: ...}}` shape; checks are off while unset or missing |
| `PLUGIN_UPDATE_GITHUB_TOKEN` | unset | GitHub token with read access to the mirror repos' releases; kept server-side as a secret |
| `PLUGIN_UPDATE_GITHUB_API_URL` | `https://api.github.com` | GitHub API base URL (GitHub Enterprise); must be `https`, except `http` on a loopback host |
| `PLUGIN_UPDATE_CHECK_INTERVAL_SECONDS` | `21600` | Seconds between automatic checks (minimum `300`) |
| `SYSTEM_UPDATE_REPO` | `baselithcore/baselithcore` | GitHub `owner/repo` whose releases and security advisories are compared with the running framework; empty disables the system notice |
| `PLUGIN_UPDATE_CACHE_DIR` | `data/plugin_updates` | Cached last report and verified release artifacts; the Helm chart sets `/tmp/plugin_updates`, since its root filesystem is read-only |
| `PLUGIN_UPDATE_MAX_ARTIFACT_MB` | `200` | Largest release artifact downloaded; it may unpack to at most four times this and 20 000 members, or it is refused before any signature check |
| `BASELITH_PLUGIN_OVERLAY_DIR` | unset | Directory of updated plugins that shadows the bundled `plugins/<name>` |
| `BASELITH_PLUGIN_TRUST_STORE` | unset | JSON trust store of signing keys (expiry, revocation) |
| `BASELITH_PLUGIN_TRUST_ROOTS` | unset | Legacy list of trusted public keys |

## System update notice

The same periodic check also compares the running framework version
(`core._version.__version__`) with the latest stable GitHub Release of
`SYSTEM_UPDATE_REPO`. The report gains a `system` section: the latest release,
how many releases the deployment is behind, whether the jump crosses a major
version, and the published GitHub Security Advisories whose vulnerable range
contains the installed version (highest severity wins; `security: true` on any
match). A downstream distribution that versions independently sets
`SYSTEM_UPDATE_REPO` to its own repo (with a token that can read it, if it is
private); an empty value disables the system check.

This is a notice only: the framework is never installed or upgraded by this
code, and only the GitHub API of the configured repo is contacted. Advisories
need a token that can read them (`PLUGIN_UPDATE_GITHUB_TOKEN`); without that
scope the update is still reported and `system.error` says the advisories are
unavailable. A private repository publishes no security advisories
(GitHub answers 404): that is not an error, and only release notices appear
for it. The plugin part and the system part fail independently, and the
service starts when either is configured. A new system version emits
`system.update_available` once on the event bus.

### Metric and alerts

Each check (and the service's start, from the cached report, so a restart does
not blank the alert) writes the gauge `baselith_update_available{component,security}`:
`1` for every available update, `0` for the other series. `component` is
`core` or `plugin:<name>`; `security` is `"true"` only for a core update an
advisory affects (plugin updates carry no advisory data, so they are
`"false"`). An advisory that affects the running version sets the
`security="true"` series even when no fixed release exists yet, so the security
alert fires before a fix ships. Series are never removed, because removal does not reach the other
workers' files in multiprocess mode; a cleared update exports `0`. There the
gauge uses `mostrecent`, so the newest write wins: a recycled worker's fresh 0
supersedes a dead worker's stale 1, and two workers do not add up to 2. A
plugin dropped from the sources entirely is zeroed by the process that saw it
before, but a process started after the removal never emits it.

The Helm chart's `PrometheusRule` adds them as a separate group:
`BaselithcoreUpdateAvailable` (info, `for: 1h`) and
`BaselithcoreSecurityUpdateAvailable` (warning, `for: 5m`). Either can be
silenced through `prometheusRule.disabledAlerts`.

## Overlay directory

The overlay is where updated plugins will live once installation ships. A
plugin present there takes precedence over the bundled copy:

```text
<overlay>/
  <name>  ->  .store/<name>-<version>/    (symlink, switched atomically)
  .store/
    <name>-<version>/
```

Removing `<overlay>/<name>` falls back to the bundled version. Activation is
always a process restart, never a hot reload.

## Refusal reasons

| Value | Meaning |
|---|---|
| `manifest_invalid` | The release manifest is missing or unreadable |
| `name_mismatch` | The manifest names a different plugin |
| `version_mismatch` | The manifest version differs from the release tag |
| `integrity_mismatch` | The recomputed hash differs from `integrity_sha256` |
| `signature_invalid` | The signature is missing or not valid for any trusted key |
| `no_trusted_keys` | The deployment has no usable trusted key |
| `not_newer` | The release is not newer than the installed version |
| `incompatible_core` | The running core is outside the plugin's supported range |
| `needs_environment_update` | A required Python dependency is not satisfied |
| `artifact_missing` | The release has no artifact for the plugin |
| `artifact_checksum` | The downloaded artifact does not match its checksum |
| `source_error` | The release source could not be queried |

## API

Both routes require an administrator.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/plugins/updates` | `{"enabled": bool, "report": ...}`: the last saved report, without network access |
| POST | `/api/plugins/updates/check` | Run a check now and return the fresh report; `503` when updates are not configured. Within 60 s of the last completed check in that worker process, the last report is returned and GitHub is not contacted |

The report carries `checked_at`, one candidate per plugin (installed version,
latest release, `available`, `refusal`, `detail`) and an `error` field. When a
whole check fails, the previous candidates are kept and `error` names the
failure type only; credentials never appear in it.
