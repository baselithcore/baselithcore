---
title: Plugin Updates
description: Plugin release detection, the two trust modes and the admin API
---

<!-- markdownlint-disable-file MD046 -->

The `core/plugin_updates` module watches GitHub releases and tells
administrators when a newer version exists: of the core itself (the system
update notice, on by default) and of your plugins (only once a sources file is
configured).

What it contacts: `api.github.com` (or `PLUGIN_UPDATE_GITHUB_API_URL`), for the
releases and published security advisories of `CORE_UPDATE_REPO`, about 60
seconds after boot and then every `PLUGIN_UPDATE_CHECK_INTERVAL_SECONDS`. Each
API worker process runs its own check. The requests are plain reads of release
data: nothing about the deployment (its version, plugins, hosts or users) is
sent beyond what any HTTPS request carries (the source address, the HTTP
client's user agent and, when configured, the token). Set `CORE_UPDATE_REPO=""`
to switch the system check off; with no sources file either, the module never
contacts GitHub.

!!! warning "Upgrade note"
    Earlier releases stayed silent until a sources file was configured. From
    this release on, a deployment with default settings contacts the GitHub API
    for the core's releases and advisories. An air-gapped or egress-filtered
    deployment should set `CORE_UPDATE_REPO=""` (or allow `api.github.com`),
    otherwise each check fails and the report carries the error.

!!! note "Notify and instruct, never execute"
    The framework never upgrades itself and never installs a plugin. When a
    newer release exists, it tells the administrator, and for the core it
    serves the exact instructions to upgrade this deployment with the standard
    tools of its installation method (Helm, Docker Compose, pip, a source
    checkout, or the operator's own procedure). The administrator runs them. See
    [Upgrade instructions](#upgrade-instructions).

## What it does

Every six hours (and on demand) the service reads the sources file, asks each
plugin repository for its latest release and judges it under the configured
[trust mode](#trust-modes): by default its provenance (who created the
release, and what the manifest says at the tagged commit), or, when
`PLUGIN_UPDATE_TRUST=signed`, by downloading the signed artifact and running the
full verification. A release that passes is reported as `available`; one that
fails is reported with the reason it was refused. The
last report is cached on disk, and the `plugin.update_available` event is
emitted on the EventBus once per new version, once per deployment rather than
once per worker. The first process to see a version takes a ten-minute lease on
it with Redis `SET NX` when `CACHE_BACKEND=redis`, or with a lock file under
`<PLUGIN_UPDATE_CACHE_DIR>/announced/` otherwise, and records a long-lived
`done` marker once the event went out. A process that dies mid-announcement, or
an emit that fails, leaves no marker: the lease is released or expires and the
next check announces again. A Redis error falls back to the lock file; a lock
file that cannot be written announces anyway. Keys are namespaced by
`PLUGIN_UPDATE_INSTANCE_ID` (default: the `APP_BASE_URL` host) so deployments
sharing one Redis do not suppress each other; with neither set the key is
shared and a warning is logged once when Redis is in use. Replicas that share neither Redis
nor a cache directory each announce once. When the cache directory is not
writable the failure is logged (`plugin_update_cache_write_failed`) and the
metric and events still go out.

## Trust modes

`PLUGIN_UPDATE_TRUST` chooses what makes a plugin release trusted enough to be
offered. Either way, the result is a notice: installing a release is always a
manual step of the deployment's own procedure.

| Mode | Offered when | Downloads | Needs |
|---|---|---|---|
| `provenance` (default) | the release was created by a workflow token from a commit on the plugin repository's default branch, and its manifest at the tagged commit agrees with it | nothing | a token that can read the repositories' releases and contents |
| `signed` | the release carries a signed `release.json` and tarball that pass the full [verification](#signed-mode-verification) | the tarball | a trust store of publisher keys, and a publisher that signs |

A value that is neither is treated as `signed`, the stricter mode, with a
warning.

### Provenance mode

Each plugin repository carries a release workflow that runs on every push to
its default branch: it reads `version` from the plugin manifest and, when no
tag `v<version>` exists and the version is newer than every existing
`v<X.Y.Z>` tag, tags the pushed commit and creates a GitHub Release with the
repository's own `GITHUB_TOKEN`. No signing key exists anywhere, so none can
be exfiltrated from CI; the trust root is the repository's own write access,
which is also what decides what the plugin's code is.

A release is offered when, in order:

1. It is the latest release that is neither a draft nor a prerelease, its tag
   is `v<X.Y.Z>`, and that version is newer than the installed one.
2. Its author is the Actions bot — login `github-actions[bot]`, type `Bot`
   and, on github.com, id `41898282` (a GitHub Enterprise Server gives the
   bot an id of its own, so the id is not pinned there): it was created by a
   workflow token, not by a person (`untrusted_release_author` otherwise).
3. The tag still points at the commit the release was created from, when the
   release names one (`tag_moved` otherwise). An annotated tag is peeled.
4. That commit is on the repository's default branch: GitHub's comparison of
   the commit with the default branch (`/compare/<sha>...<branch>`) reports
   `ahead` or `identical` (`not_on_default_branch` otherwise). Any workflow
   run with `contents: write` — including one on a side branch running a
   modified workflow — can create a bot-authored release of any commit, and a
   release's `target_commitish` is whatever its creator set, so neither the
   author nor the release's own target proves the commit was merged.
5. The plugin manifest **at that commit**, read through the contents API
   (`manifest.yaml`, `.yml`, then `.json`, the loader's order, at most 1 MiB,
   from the configured API host only), declares the same `name` and `version`,
   a stable version, and `min_core_version`/`max_core_version` that admit the
   running framework, the rule the plugin loader applies (`name_mismatch`,
   `version_mismatch`, `manifest_invalid`, `incompatible_core` otherwise).

The plugin's `python_dependencies` are not judged here: the release is
installed by rebuilding the image or reinstalling the package, which brings
its dependencies with it.

Each candidate reports `trust` and `provenance`: the release author, the
commit SHA, a link to that commit on the repository's web host (`github.com`
for `api.github.com`, `https://<host>` for a GitHub Enterprise
`https://<host>/api/v3`, none otherwise) and the publication time. Consoles
show "Published by <author> from commit <sha> on <date>".

A saved verdict is served only under the mode that reached it: after
`PLUGIN_UPDATE_TRUST` changes, the previous mode's candidates are dropped
(also when a failed check would otherwise carry them over) until the next
check replaces them.

#### What provenance trust proves

A provenance notice means *the plugin repository's default branch contains
this version*, and nothing more:

- **Provenance trust equals write access to the plugin repository's default
  branch.** Everyone who can merge there — outside contributors of a
  repository you share included — can cause a notice on every deployment that
  watches it.
- **A notice may announce code that your own review and gates have not seen
  yet**, when the plugin repository is fed back into a codebase you build
  from; the notice says nothing about review, tests or signatures.
- **Install only from artifacts you built** (an image or package produced by
  your own pipeline after that review), never straight from a repository tag.
  When the release itself must be verifiable, run `PLUGIN_UPDATE_TRUST=signed`.

Two behaviours of a self-releasing repository are worth knowing. A tag higher
than the manifest version (a hand-pushed `v99.0.0`) stops the workflow from
releasing anything newer, and deployments keep reporting that highest release
(refused as `untrusted_release_author` when a person made it): delete the
stray release and its tag, then push again. And rapid pushes may skip an
intermediate version — one pending run per repository, replaced by the newest
push — which is harmless: the next release carries its changes.

### Signed mode verification

Only signed releases are offered in this mode. The verifier reuses the plugin signing
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
fails rule 3. Keep it off CI runners whose secrets anyone with push access can
read; that is the reason the provenance mode is the default. A signed release
may be the same GitHub Release the repository's workflow created, with the
signed assets attached to it, so it passes both modes.

## Release manifest

Each release carries `release.json` next to its tarball. From release format 2
it lists the SHA-256 of **every** file in the tarball under `files` (paths
relative to the plugin directory — documentation, locales, templates and any
`wheelhouse/` included, not only the files the plugin hash covers) and signs
itself: `manifest_signature_ed25519` is an Ed25519 signature, by the same
trusted publisher key, over the canonical JSON of every other key (sorted keys,
no whitespace, ASCII, prefixed with `baselith-release-manifest-v1\n`). Before
downloading the tarball the checker refuses a `release.json` without both
fields (`legacy_release`: the release is listed but cannot be installed) or
whose signature does not verify; after unpacking, any missing, extra or changed
file is `files_mismatch`.

The checker is strict about the shape as well. `release.json` must be a single
JSON object without duplicate keys, `NaN`/`Infinity` or any floating-point
number (the canonical text of a float would depend on the Python that prints
it), and `release_format` must be the integer `2`. A `files` key must be a plain relative POSIX path (no
empty, `.` or `..` segment, no leading `/`, no backslash, no control
character), and two keys naming one file on a case-insensitive or
Unicode-normalising filesystem are refused as ambiguous; any of these is
`manifest_invalid`. The tarball may hold only regular files and directories,
each path once (compared ignoring case and Unicode normalisation): a symbolic or
hard link, a device, a FIFO or a colliding path is refused before anything is
extracted.
An empty directory anywhere in the unpacked tree is `files_mismatch`: a file
list cannot show it, yet it can turn an import into a namespace package.

`core.plugin_updates.checker.verify_release_tarball` runs this same unpack and
verification for callers outside the checker; the mirror release step uses it as
a self-check, with a syntax-only dependency predicate so a release never depends
on the publisher's installed packages.

Verified tarballs are cached under
`<PLUGIN_UPDATE_CACHE_DIR>/tarballs-v2/`, and the saved report carries a
`cache_format`. A report saved before the signed file list existed loads
without its plugin candidates, so none is served until the next check
completes. Its core update notice is kept, a security notice included, and a
failed check still carries it over. The service deletes the old `tarballs/`
directory when it starts. A tarball verified under the old rules
is therefore never offered, and neither is an `available` verdict about it. Any
refusal also removes the cached tarball of the refused version.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PLUGIN_UPDATE_SOURCES_FILE` | unset | YAML file in the `mirrors: {name: {repo: ...}}` shape; checks are off while unset or missing |
| `PLUGIN_UPDATE_TRUST` | `provenance` | `provenance` or `signed`: which [trust mode](#trust-modes) a plugin release must pass; any other value means `signed` |
| `PLUGIN_UPDATE_GITHUB_TOKEN` | unset | GitHub token with read access to the plugin repos' releases and contents; kept server-side as a secret |
| `PLUGIN_UPDATE_GITHUB_API_URL` | `https://api.github.com` | GitHub API base URL (GitHub Enterprise); must be `https`, except `http` on a loopback host |
| `PLUGIN_UPDATE_CHECK_INTERVAL_SECONDS` | `21600` | Seconds between automatic checks (minimum `300`) |
| `CORE_UPDATE_REPO` | `baselithcore/baselithcore` | GitHub `owner/repo` of the public core project, whose releases and security advisories are compared with the running core release; empty disables the system notice |
| `SYSTEM_UPGRADE_GUIDE_URL` | unset | `https` link to this deployment's upgrade instructions, served with the system notice; any other value is ignored |
| `SYSTEM_INSTALL_METHOD` | detected | `helm`, `docker`, `pip`, `source` or `custom`: which [upgrade instructions](#upgrade-instructions) the notice serves; unset detects it, any other value is ignored with a warning |
| `SYSTEM_UPGRADE_INSTRUCTIONS_FILE` | unset | Markdown file with this deployment's own upgrade procedure (method `custom`); `{version}` and `{current}` are filled in; empty means unset |
| `PLUGIN_UPDATE_CACHE_DIR` | `data/plugin_updates` | Cached last report and verified release artifacts; the Helm chart sets `/tmp/plugin_updates`, since its root filesystem is read-only |
| `PLUGIN_UPDATE_MAX_ARTIFACT_MB` | `200` | Largest release artifact downloaded; it may unpack to at most four times this and 20 000 members, or it is refused before any signature check |
| `BASELITH_PLUGIN_OVERLAY_DIR` | unset | Directory of updated plugins that shadows the bundled `plugins/<name>` |
| `BASELITH_PLUGIN_TRUST_STORE` | unset | JSON trust store of signing keys (expiry, revocation) |
| `BASELITH_PLUGIN_TRUST_ROOTS` | unset | Legacy list of trusted public keys |

## System update notice

The same periodic check compares the running core with the latest stable
GitHub Release of the public core project, `CORE_UPDATE_REPO`. The installed
version is `CORE_VERSION` from `core/_core_version.py`: the public core release
the running `core/` tree corresponds to. The report gains a `system` section
(`component: "core"`): the latest release, how many releases the deployment is
behind, whether the jump crosses a major version, and the published GitHub
Security Advisories whose vulnerable range contains the installed version
(highest severity wins; `security: true` on any match).

**Why `CORE_VERSION` and not `__version__`.** In the core project the two are
the same release: its release job rewrites `core/_version.py` and
`core/_core_version.py` together, and `tests/unit/core/test_core_version.py`
fails if they disagree. A downstream distribution that ships this `core/`
beside components of its own versions independently. It marks its
`core/_version.py` with `__distribution__ = "<name>"`, keeps its own version
there and receives `core/_core_version.py` unchanged through its normal core
alignment (its release job must never rewrite it; the same test checks that).
The notice therefore always says which public core release a deployment runs
and whether a newer one, or a security advisory against it, exists, whatever
the distribution's own version number. `SYSTEM_UPDATE_REPO`, which once
pointed the notice at another repository, is ignored: when it is still set, a
single warning is logged at startup.

This is a notice only: the core is never installed or upgraded by this code,
and only the GitHub API of the configured repo is contacted. The public repo
needs no token. When `PLUGIN_UPDATE_GITHUB_TOKEN` is set (plugin mirrors need
it), it is sent to the same API host for the core requests too: a read-only
token on a public repository grants nothing extra, and an authenticated client
gets a much higher rate limit. Without advisory read access the update is still
reported and `system.error` says the advisories are unavailable. A repository
that publishes no security advisories answers 404: that is not an error, and
only release notices appear for it. A 404 never clears a known notice: a
previously reported security state is carried over, and only a successful
fetch that no longer matches clears it. A saved notice about another repository
or another installed version is never served (after an upgrade, the first check
replaces it). The plugin part and the system part fail independently, and the
service starts when either is configured. A new core version emits
`system.update_available` (with `component: "core"`) once on the event bus.

**How to upgrade.** The notice never claims how the deployment is upgraded:
it serves [upgrade instructions](#upgrade-instructions) for the installation
method, and `SYSTEM_UPGRADE_GUIDE_URL`, when set, is carried as
`system.upgrade_guide_url` (read from the current configuration every time the
report is served) for a console to link as "How to upgrade".

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

## Upgrade instructions

The product is a notification plus version-specific instructions, the model
GitLab follows: the console tells the administrator that a release exists and
shows how to upgrade to that exact release; the administrator performs the
upgrade with the installation's own tools. Nothing is ever executed on their
behalf, so the web process needs no permission to change the deployment.

When a newer core release exists, the served notice carries `system.upgrade`
(never cached; derived from the current configuration on every read), and
`GET /api/plugins/updates`, the console and any client show the same thing:

| Field | Meaning |
|---|---|
| `method`, `detected` | `helm`, `docker`, `pip`, `source` or `custom`, and whether it was detected rather than set |
| `current_version`, `target_version`, `latest_version` | Installed release, the release these steps install, and the latest release |
| `path` | Stops of an upgrade across major versions (below); empty for a direct upgrade |
| `steps` | Ordered steps: `id` (stable, for translation), `text`, optional `command` (shown for copying, never run) |
| `checklist` | Backup, release notes, plugin compatibility, then the post-upgrade checks |
| `plugins` | Installed plugins whose declared core bounds exclude `target_version` |
| `guide_url`, `release_notes_url` | The operator's guide (`SYSTEM_UPGRADE_GUIDE_URL`) and the target release's notes |
| `custom_text`, `custom_error` | The operator's own procedure (method `custom`) or why it could not be read |
| `distribution` | The downstream distribution this deployment runs, if any (below) |
| `image` | The published image of the target release (method `docker`) |

**Installation method.** `SYSTEM_INSTALL_METHOD` wins. Unset, a configured
`SYSTEM_UPGRADE_INSTRUCTIONS_FILE` means `custom`; otherwise
`KUBERNETES_SERVICE_HOST` means `helm`, a container (`/.dockerenv`,
Podman's `/run/.containerenv`, or a `docker`/`containerd`/`kubepods`/
`libpod`/`podman` cgroup for PID 1) means `docker`, a source checkout (the
`core` package outside `site-packages`, as with `pip install -e .`, or a
`.git` beside it) means `source`, and anything else `pip`. The detection runs
once per process; set `SYSTEM_INSTALL_METHOD` when it guesses wrong.

**Upgrade path.** Within one major version the upgrade is direct. Across majors
it goes one major at a time: the latest release of the current major (when
newer than the installed one), the latest release of each intermediate major,
then the latest release. The steps install the first stop; after it, the next
check shows the next one. The path is computed from the releases the check
already reads and is kept (`system.upgrade_path`) in the cached report.

**Default templates** (`X` is the target release, `owner/repo` the
`CORE_UPDATE_REPO`; words in angle brackets are values only the administrator
knows):

=== "helm"

    ```bash
    helm list --all-namespaces
    git clone --depth 1 --branch vX https://github.com/owner/repo.git baselithcore-X
    helm upgrade <release> ./baselithcore-X/deploy/helm/baselithcore \
      --namespace <namespace> --reset-then-reuse-values \
      --set image.tag=X --wait --timeout 15m
    helm test <release> --namespace <namespace>
    ```

    The chart is installed from the core repository at the release tag (no
    Helm repository is published). `--reset-then-reuse-values` (Helm 3.14+)
    keeps your values on top of the new chart's defaults, where
    `--reuse-values` would ignore defaults the new chart adds. The chart's
    pre-upgrade Job runs the migrations. Backup: `kubectl create job
    --namespace <namespace> --from=cronjob/<fullname>-backup
    baselithcore-before-X-Y-Z` (the chart's `backup.enabled` CronJob; the
    Job name carries the version with dashes, as Kubernetes names require). The pod's
    namespace is filled in when it can be read from the service-account mount.

=== "docker"

    ```bash
    ./scripts/backup-db.sh   # from the checkout; uses compose.prod.yaml, writes /backups (root)
    # A: the compose file builds the image from a source checkout
    #    (compose.yaml and compose.prod.yaml both do)
    git fetch --tags && git checkout vX && \
      docker compose -f compose.prod.yaml build && \
      docker compose -f compose.prod.yaml up -d
    # B: or run the published image: set `image: ghcr.io/owner/repo:X` on
    #    the api and worker services (in place of `build:`), then
    docker compose -f compose.prod.yaml pull api worker && \
      docker compose -f compose.prod.yaml up -d
    # only with DB_MIGRATIONS_ON_STARTUP=false:
    docker compose -f compose.prod.yaml exec api baselith db migrate
    ```

    The commands name `compose.prod.yaml`, the production stack; use your own
    compose file if it differs (a bare `docker compose` would act on the
    development `compose.yaml`). The two alternatives are exclusive: build
    from the tag, or pull the published image.

=== "pip"

    ```bash
    pg_dump --format=custom --file=baselith-before-X.dump <database-url>
    pip install --upgrade "baselith-core==X"
    baselith db migrate
    # then restart the API and worker processes
    ```

=== "source"

    ```bash
    pg_dump --format=custom --file=baselith-before-X.dump <database-url>
    git fetch --tags && git checkout vX
    pip install -e .   # keep your extras, e.g. .[rag]
    baselith db migrate
    # then restart the API and worker processes
    ```

    A git checkout of the core repository on a host, running from the
    checkout rather than an installed wheel.

=== "custom"

    The operator's markdown file (`SYSTEM_UPGRADE_INSTRUCTIONS_FILE`, at most
    64 KiB) with `{version}` and `{current}` replaced; nothing else in it is
    interpreted. A console renders it without raw HTML, and links only to
    `https` URLs. Only a regular file is read (a FIFO or a directory is
    refused), and the read is reused until the file changes. This is how a
    deployment whose procedure differs from the public distribution's (a
    GitOps repository, an internal pipeline) documents its own steps.

Every method's checklist ends with `curl -fsS <APP_BASE_URL>/health/ready` and
checking that the notice now shows the new release as installed.

**Downstream distributions.** A distribution that marks its
`core/_version.py` with `__distribution__` gets no public core steps, whatever
the method: installing the public package, image or chart would replace the
distribution with the public release. The instructions carry
`distribution` and a single step saying that its own procedure applies and
how to show it (`SYSTEM_UPGRADE_INSTRUCTIONS_FILE`, which switches the method
to `custom`, or a `SYSTEM_UPGRADE_GUIDE_URL` link); the backup, release notes
and post-upgrade checks stay.

**Robustness.** The instructions and the plugin install guidance are derived
on every read; a failure there is logged once and the report is served
without that part (`upgrade: null`, or `plugins.checked: false` when the
installed plugins' manifests cannot be read), never as an error.

**Plugin compatibility.** The checklist lists installed plugins whose
`min_core_version`/`max_core_version` exclude the target release, so they are
updated or removed first. The bounds are compared with the version
`core._version` reports (see the plugin loader); in the core project that is the
public core release, so the check is exact. A downstream distribution marks its
`core/_version.py` with `__distribution__` and versions it independently, so
its plugins' bounds do not speak about the public core release: there the check
is reported as not computed (`plugins.checked: false`), rather than flagging
every plugin.

**Plugin updates.** No console action and no command installs a plugin
release. Each available plugin candidate carries `install` (`method`,
`guide_url`, `automated: false`): a console says so and points to how plugins
reach this installation (in the image for Helm and Docker Compose, the plugin
directory on a host or in a source checkout) and to the operator's guide.

## Overlay directory

The overlay directory holds plugins installed on top of the bundled ones. A
plugin present there takes precedence over the bundled copy:

```text
<overlay>/
  <name>  ->  .store/<name>-<version>/    (symlink, switched atomically)
  .store/
    <name>-<version>/
```

Removing `<overlay>/<name>` falls back to the bundled version. Activation is
always a process restart, never a hot reload.

An entry is registered only when it is **newer than the bundled plugin** of the
same name and its `min_core_version`/`max_core_version` accept the running
core. Otherwise it is logged (`refused (not_newer: overlay 1.3.0 <= bundled
1.5.0)` or `refused (incompatible_core: ...)`) and the bundled plugin loads, so
an image upgrade is never undone by an old overlay entry. An operator
removes such entries with `core.plugins.overlay_prune.prune_stale_overlay`;
plain directories at the overlay root are reported, never deleted.

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
| `legacy_release` | The release predates the signed file list; it is shown but cannot be installed |
| `files_mismatch` | The unpacked files differ from the signed file list |
| `source_error` | The release source could not be queried |
| `untrusted_release_author` | Provenance mode: the release was not created by the repository's release workflow |
| `tag_moved` | Provenance mode: the release tag no longer points at the commit the release was created from |
| `not_on_default_branch` | Provenance mode: the release commit is not on the repository's default branch |

## API

Both routes require an administrator.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/plugins/updates` | `{"enabled": bool, "report": ...}`: the last saved report, without network access |
| POST | `/api/plugins/updates/check` | Run a check now and return the fresh report; `503` when updates are not configured. Within 60 s of the last completed check in that worker process, the last report is returned and GitHub is not contacted |

The report carries `checked_at`, one candidate per plugin (installed version,
latest release, `available`, `refusal`, `detail`, `trust` and `provenance`) and
an `error` field. When a
whole check fails, the previous candidates are kept and `error` names the
failure type only; credentials never appear in it.
