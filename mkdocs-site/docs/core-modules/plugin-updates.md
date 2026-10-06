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

!!! note "Notify and instruct; plugins alone may be installed, and only on a host"
    The framework never upgrades itself. When a newer release exists, it tells
    the administrator, and for the core it serves the exact instructions to
    upgrade this deployment with the standard tools of its installation method
    (Helm, Docker Compose, pip, a source checkout, or the operator's own
    procedure). The administrator runs them. See
    [Upgrade instructions](#upgrade-instructions). The one exception is a
    maintainer-signed **plugin** release on a host install, which an operator
    may switch on: see
    [One-click plugin updates (host installs)](#one-click-plugin-updates-host-installs).
    It is off by default, and the web process never installs anything itself.

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

### Module map

| File | Role |
| --- | --- |
| `archive.py` | Unpacks a release tarball under the member and size limits, and verifies it exactly as a deployment does (`verify_tarball`, `verify_release_tarball`) |
| `signed_assets.py` | `verify_signed_assets`: downloads a release's `release.json` and tarball, checks the signature, checksum and file list, and keeps the verified tarball in the cache; the outcome is a `SignedAssets` record |
| `checker.py` | Per-plugin and whole-deployment checks under the configured trust mode |

## Trust modes

`PLUGIN_UPDATE_TRUST` chooses what makes a plugin release trusted enough to be
offered. Either way, the result is a notice: installing a release is a manual
step of the deployment's own procedure, unless the release also carries signed
assets and the host runs the
[one-click updater](#one-click-plugin-updates-host-installs).

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

#### Installable releases

Independently of the trust mode, when an available release carries both signed
assets (`<plugin>-<version>.tar.gz` and `release.json`) the check verifies them
against the trusted publisher keys and reports the
verdict as `signed_assets` on the candidate, cached with the report. A verified
release has its tarball kept in the cache and reports `tarball_sha256`, the
signed file count and `host_build_required`; a release without assets reports
`artifact_missing` ("no signed assets attached"), a foreign or bad signature
`signature_invalid`. In provenance mode this never changes the notice: the
candidate stays `available`, it merely also says whether it is *installable*.
A candidate whose manifest sets `host_build_required: true` is never
one-click installable.

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
3. `signature_ed25519` verifies against a usable trusted key **whose scope
   covers this plugin** (the trust store's per-key `plugins` list,
   `load_trust_roots(plugin_name)`). Unsigned or unknown-key releases are
   refused regardless of `BASELITH_REQUIRE_PLUGIN_SIGNATURES`; the update path
   is always strict.
4. The manifest `name` is the plugin being updated and its `version` equals the
   tag.
5. No downgrade: the version must be strictly greater than the installed one.
6. The running core satisfies the plugin's `min_core_version` and
   `max_core_version`.
7. Every `python_dependencies` requirement is already satisfied in the running
   environment.

The trust roots are looked up per plugin everywhere they are used — by the
checker (`run_check` accepts either one key list or a `plugin name -> keys`
callable), by the executor before staging, and by the overlay loader — so a
publisher key scoped to one plugin cannot vouch for another. See
[Plugin trust store](../advanced/security.md#plugin-trust-store).

**Metadata is size-capped.** Release lists, tags, advisories and `release.json`
are small JSON documents: every metadata response and the `release.json`
download are read through a **1 MiB** cap (`MAX_TEXT_FILE_BYTES`), streamed and
cut off past it rather than buffered, so a compromised or misbehaving API host
cannot exhaust the checker. Tarballs keep the separate
`PLUGIN_UPDATE_MAX_ARTIFACT_MB` cap.

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
only release notices appear for it. An advisory whose vulnerable range is
missing or does not parse, or an installed version that does not parse, cannot
rule the advisory out, so it is **reported as affecting** and logged as
`system_update_range_uncertain` — it used to be dropped from the notice. A 404 never clears a known notice: a
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
updated or removed first. Plugins declare those bounds against the public
core release (`CORE_VERSION` in `core/_core_version.py`) in every distribution,
the same number the plugin loader's compatibility gate compares with, so the
target compares with them directly and the check is exact in a downstream
distribution too. It is reported as not computed (`plugins.checked: false`)
only when the installed plugins' manifests cannot be read.

**Plugin updates.** Each available plugin candidate carries `install`
(`method`, `guide_url`, `automated: false`): the instructions describe how
plugins reach this installation (in the image for Helm and Docker Compose, the
plugin directory on a host or in a source checkout) and point to the
operator's guide. A host install may additionally enable
[one-click plugin updates](#one-click-plugin-updates-host-installs); the
instructions stay the fallback wherever a release is not installable that way.

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

An accepted entry is imported from its **resolved** store directory
(`.store/<name>-<version>/`), not through the `<name>` link. The link is
switched atomically by an update while the process runs; resolving it once,
after verification, guarantees that only the tree just verified ever backs a
lazy submodule import. Each entry is verified against the trust roots scoped
to its own plugin name.

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

## One-click plugin updates (host installs)

On a host install an operator may let administrators install a newer plugin
release, and roll it back — from the host shell with `baselith
plugin-updater`, or from an external console if one is installed (the core
ships none) — instead of pulling the code by hand.
The core itself is never updated this way. The web process only records a
request: a separate updater process verifies, installs, restarts the API,
checks its health and rolls back on its own. It is **off by default**
(`UPDATE_APPLY_ENABLED=false`). The sections after this one describe each
module; this one is the operator's view.

### When a release can be installed

A release is installable only when all of these hold. The console says which
ones fail, one line per blocker, and keeps showing the manual instructions.

- The release is **signed by the maintainer**: it carries `release.json`
  (format 2, with its manifest signature) and `<plugin>-<version>.tar.gz`, and
  both verify against this deployment's trust store
  ([Installable releases](#installable-releases)). A provenance-only release
  stays a notice.
- The deployment is a **host install** (`source`, `pip`, or `custom` on a host) with an overlay
  directory the updater can write, and the updater is running the same core
  version as the API.
- Every `python_dependencies` requirement is already satisfied and the
  release does not declare `host_build_required` (it needs no build on the
  host).
- No other run of that plugin is in progress.

| Blocker | Meaning | What to do |
| --- | --- | --- |
| `apply_disabled` | `UPDATE_APPLY_ENABLED` is off, or the live updater reports itself disabled or without a restart command | Set the switch and `UPDATE_APPLY_RESTART_COMMAND` for the API and the updater, restart both |
| `not_host_install` | Install method `helm` or `docker`, `KUBERNETES_SERVICE_HOST` set, or a container detected, whatever `SYSTEM_INSTALL_METHOD` says | None: use the instructions (see [why Kubernetes is excluded](#why-kubernetes-is-excluded)) |
| `overlay_unconfigured` | `BASELITH_PLUGIN_OVERLAY_DIR` unset, missing or not writable by the updater | Create the directory, owned by the service user, and set the variable for both units |
| `updater_offline` | No updater heartbeat within three `UPDATE_APPLY_HEARTBEAT_SECONDS` | Start the updater unit; `baselith plugin-updater status` shows the heartbeat |
| `updater_mismatch` | The updater runs another core version than the API | Restart the updater after pulling a new core |
| `unsigned_release` | The release has no signed assets | Publish a signed release, or install by hand |
| `signature_failed` | Signed assets present but refused; the refusal code is in the detail | Fix the trust store or the release |
| `needs_environment_update` | A Python dependency of the release is not installed | Install the dependency, or install by hand |
| `host_build_required` | The release needs a build on the host (a Node sidecar, for example) | Install by hand |
| `run_active` | A run for this plugin is not finished yet | Wait for it, or see `baselith plugin-updater status` |

### What happens during a run

!!! note "The core ships no console"
    The core creates runs only through the CLI (`baselith plugin-updater
    request` / `rollback`), and every such run is **pre-approved**: whoever
    has a shell on the host is trusted, so it goes straight to step 3.
    Nothing in the core creates a run in `awaiting_approval`, demands an MFA
    code or asks a second administrator. Steps 1–2 below — and the
    `awaiting_approval`, `expired` and `denied` states — apply only when an
    external console (a separate plugin or service) writes runs to the same
    store under `UPDATE_APPLY_STATE_DIR`, and their MFA and four-eyes rules
    are that console's, not the core's.

1. **Request** (external console only). An administrator asks for version *V*, pinned to the tarball
   SHA-256 they were shown. The console demands a fresh MFA code (an account
   without an enrolled authenticator, and any API key, is refused) and
   re-checks installability on the server. The run starts in
   `awaiting_approval`.
2. **Approval** (external console only). A second administrator approves it with their own MFA code.
   Whether the requester may approve their own request is the console's
   policy (a console enforcing four-eyes refuses it). A request nobody
   approves expires after the console's approval window (`expired`); a
   denied one ends `denied`; an approval that finds the served release
   changed since the request refuses it and expires the request at once, so
   the plugin is free for a new one. The CLI path, run by someone with a
   shell on the host, skips this step.
3. **Install.** The updater picks up the approved run, re-downloads the signed
   `release.json`, refuses anything that differs from the pinned SHA-256,
   refuses a release whose manifest sets `host_build_required`, stages the
   release into the overlay store, carries over the plugin's `.env` and
   declared runtime state, and runs `schema-init` for that plugin as the
   schema owner — against a scratch copy of the overlay where the plugin
   already resolves to the new version, while the running API still
   resolves the old one. A failed `schema-init` ends the run
   `migration_failed` with nothing changed on the running API.
4. **Restart and health.** It records `restart_at`, switches the plugin's
   link, runs `UPDATE_APPLY_RESTART_COMMAND` and waits until every API worker that booted
   after `restart_at` reports the new version active and healthy, every plugin
   that was active before is still active, and `UPDATE_APPLY_HEALTH_URL`
   answers 200 for `UPDATE_APPLY_STABLE_SECONDS` in a row.
5. **Outcome.** `succeeded`; or, when the restart or the health check fails,
   the link goes back and the API is restarted again: `rolled_back`. If that
   rollback fails too, the run ends `rollback_failed` (a CRITICAL log line and
   the `plugin.update_rollback_failed` event) and the plugin accepts no new
   run until an operator repairs it and runs
   `baselith plugin-updater resolve <run_id>` (the run then ends `failed`,
   and a consumer auditing outcomes records that one as well).

**Roll back** (the console button, or `baselith plugin-updater rollback`)
returns the plugin to the version before its newest successful update, or to
the bundled copy. It asks for MFA again but, by default, no second approval:
it is incident response. It restarts the API like an update.

!!! warning "A rollback never reverts `schema-init`"
    The previous version runs against the schema the new one created. Plugin
    schema changes must therefore be **expand-only** (additive, backward
    compatible) for at least one version. The `rolled_back` detail says
    `schema changes from <version> remain` when schema-init ran.

Two conditions make the updater refuse a run up front, before changing
anything:

- **No boot report yet.** The API writes a boot report on every start only
  while `UPDATE_APPLY_ENABLED` is on. Right after switching the feature on,
  restart the API once, or every run fails `apply_disabled` with "no boot
  report yet — restart the API once with UPDATE_APPLY_ENABLED on".
- **A schema credentials file that cannot be read.** When
  `UPDATE_APPLY_SCHEMA_ENV_FILE` is set but missing, unreadable or **group- or
  world-writable** (it must be `0600`), the run fails `migration_failed`; it
  never falls back to the runtime credentials.

**Worker count.** The health check waits for as many distinct workers as the
launcher declared. `baselith run --workers N` declares them
(`BASELITH_WEB_CONCURRENCY`); a single-process `backend.py` is one worker. An
API started as `uvicorn --workers N` directly declares nothing, so the check
would wait for one worker only: launch a multi-worker API with
`baselith run`, or set `BASELITH_WEB_CONCURRENCY` in its unit.

### The updater unit

`baselith plugin-updater serve` runs as its own systemd unit
(an example is in [systemd units](#systemd-units)), as the same user, in
the same environment and on the same checkout or virtualenv as the API, with
the same absolute `UPDATE_APPLY_STATE_DIR`:

- It is **not** `PartOf`, `BindsTo` or `Requires` the API unit: it restarts
  the API and must survive that restart to judge it and roll back. It never
  serves HTTP and never imports plugin code, so a plugin update never needs
  it restarted. After a core upgrade, restart it (otherwise
  `updater_mismatch`).
- The only privileged command it runs is the API restart, through a sudoers
  drop-in that allows exactly that command and nothing else.
- `NoNewPrivileges=true` is left off on purpose: `sudo` is setuid, so that
  option (and the sandboxing options that imply it for a non-root unit) would
  break the restart. Turn it on if the restart is granted through a polkit
  rule instead. See [systemd units](#systemd-units).

### Settings

All are read by both the API and the updater; the updater reads them at
start, so restart it after a change.

| Variable | Default | Purpose |
| --- | --- | --- |
| `UPDATE_APPLY_ENABLED` | `false` | Kill switch |
| `UPDATE_APPLY_STATE_DIR` | `data/plugin_updates/apply` | Run store, heartbeat and boot reports shared by the API and the updater; must be absolute in production |
| `UPDATE_APPLY_RESTART_COMMAND` | `[]` | argv that restarts the API — a JSON array or a comma-separated list — run without a shell |
| `UPDATE_APPLY_RESTART_TIMEOUT_SECONDS` | `60` | Timeout of the restart command (5–600) |
| `UPDATE_APPLY_HEALTH_URL` | `http://127.0.0.1:8000/health/ready` | Readiness URL probed after a restart |
| `UPDATE_APPLY_HEALTH_TIMEOUT_SECONDS` | `180` | Deadline of the post-restart health check (30–1800) |
| `UPDATE_APPLY_STABLE_SECONDS` | `20` | How long readiness must hold without a failure (5–300) |
| `UPDATE_APPLY_KEEP_VERSIONS` | `2` | Store entries kept per plugin (2–10) |
| `UPDATE_APPLY_SCHEMA_INIT` | `true` | Run `schema-init --plugin <name>` before the restart |
| `UPDATE_APPLY_SCHEMA_ENV_FILE` | unset | dotenv with the schema owner's database credentials, read only into the `schema-init` environment; refused unless owner-writable only (`0600`) |
| `UPDATE_APPLY_HEARTBEAT_SECONDS` | `5` | Updater heartbeat period (1–60) |
| `UPDATE_APPLY_POLL_SECONDS` | `2.0` | How often the updater looks for approved runs (0.2–30) |
| `UPDATE_APPLY_APPROVAL_TTL_SECONDS` | `86400` | Expiry of an approval request. Matters only when an external console creates runs that need approval without setting its own window; runs created by `baselith plugin-updater` (the only creator in the core) are pre-approved and carry no approval expiry |

They come on top of `BASELITH_PLUGIN_OVERLAY_DIR`, the trust store
(`BASELITH_PLUGIN_TRUST_STORE`), `PLUGIN_UPDATE_SOURCES_FILE` and the other
[plugin update settings](#configuration). A console plugin that asks for a
second approval sets the expiry of the requests it creates with its own
setting; `UPDATE_APPLY_APPROVAL_TTL_SECONDS` applies only to requests created
without one.

### Example: a source checkout under systemd

A git checkout run by systemd as user `baselith`, with the API unit
`baselithcore.service`. In the `.env` both units read:

```bash
UPDATE_APPLY_ENABLED=true
SYSTEM_INSTALL_METHOD=source
UPDATE_APPLY_STATE_DIR=/opt/baselith-data/plugin_updates/apply
BASELITH_PLUGIN_OVERLAY_DIR=/opt/baselith-overlay        # outside the checkout: git never touches it
BASELITH_PLUGIN_TRUST_STORE=/etc/baselith/plugin-trust.json
PLUGIN_UPDATE_SOURCES_FILE=/etc/baselith/plugin-sources.yaml
UPDATE_APPLY_RESTART_COMMAND=["sudo","-n","/usr/bin/systemctl","restart","baselithcore.service"]
UPDATE_APPLY_HEALTH_URL=http://127.0.0.1:8000/health/ready
UPDATE_APPLY_SCHEMA_ENV_FILE=/etc/baselith/schema-owner.env   # mode 0600, owner DSN only
```

The sudoers drop-in, and nothing broader:

```bash
echo 'baselith ALL=(root) NOPASSWD: /usr/bin/systemctl restart baselithcore.service' \
  | sudo tee /etc/sudoers.d/baselith-updater
sudo chmod 0440 /etc/sudoers.d/baselith-updater
sudo visudo -c
```

Then install and start the updater unit, restart the API once (so it writes a
boot report), and check:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now baselithcore-plugin-updater
sudo systemctl restart baselithcore.service
baselith plugin-updater status          # a fresh heartbeat, no unfinished runs
```

A console then offers the update for a signed release. Keep
the signing key off the host: only its public key sits in the trust store.

### Why Kubernetes is excluded

On Kubernetes, and in any container, the button is never offered
(`not_host_install`) and no updater runs. Images are immutable and
replicated: an overlay written inside one pod would diverge between replicas
and disappear on the next reschedule, and restarting a Deployment is the
rollout controller's job, audited in the cluster. Plugins there reach a new
version with a new image, through Helm or your image pipeline, and the console
keeps showing the instructions.

## One-click update: settings and run store

`core.config.plugin_update_apply.UpdateApplyConfig` (prefix `UPDATE_APPLY_`)
holds the settings of host-side one-click updates. The kill switch
`UPDATE_APPLY_ENABLED` is **off by default**. `UPDATE_APPLY_RESTART_COMMAND` is
an argv run without a shell, given as a JSON array
(`["sudo","-n","/usr/bin/systemctl","restart","api.service"]`) or a
comma-separated list (`sudo,-n,/usr/bin/systemctl,restart,api.service`); a
blank value means none. `UPDATE_APPLY_APPROVAL_TTL_SECONDS` (default 24 h) is
the expiry of approval requests created without their own window — only an
external console creates such requests; the core's CLI runs are pre-approved.

Settings that fail to load never take the update checker down:
`PluginUpdateService` logs the error's type and falls back to the defaults,
so one-click updates stay off until the settings are fixed.

`core.plugin_updates.apply.RunStore` is the file-based store the API workers
and the updater process share. Each plugin has at most one active run: a
per-plugin exclusive `flock` serialises every create and transition across
threads and processes, records are written by atomic replace, and every
transition is journaled to an append-only log before the snapshot changes. A
terminal state releases the claim; `rollback_failed` keeps it until an
operator intervenes.

`RunStore.transition` takes an optional `expect` (a state or a set of states)
that is compared under the plugin lock and raises `RunStateConflict` when a
concurrent change won (for example an approval arriving after the request
expired). A run in a terminal state can never be transitioned again, only a
fixed set of fields may be changed, and a torn journal line left by a crash is
skipped on read. `UPDATE_APPLY_STATE_DIR` is resolved to an absolute path at
load; a relative value is refused when the switch is on and `APP_ENV=production`.

`RunStore.runs(states=None)` lists every run of every plugin, oldest first
(optionally only those in `states`).

A consumer that audits finished runs — several web workers may share one
store — does it in three steps: `RunStore.claim_audit(run_id)` (True for one
caller only; False once the run is audited or while another caller's claim is
younger than five minutes — an older claim belongs to a worker that died and
is taken over), then writes its audit row, then
`RunStore.mark_audited(run_id)` (True exactly once per run; it ends the claim).
When the write fails it calls `RunStore.release_audit(run_id)` so the next
pass retries. `RunStore.is_audited(run_id)` reads the mark without taking it.
A run whose outcome changes after it was audited — an operator resolving a
`rollback_failed` run to `failed` — loses its mark, so the new outcome is
audited too.

`RunStore.prune_finished(now=None, older_than=90 days)` deletes finished runs
(snapshot, journal and audit markers) last updated more than `older_than` ago
that were audited, and finished runs nobody audited after a year. The newest
succeeded update of each plugin is always kept (a roll back needs it) and
`rollback_failed` runs are never pruned. The updater calls it when it starts
and then at most once an hour.

### Installability verdict

Every available plugin update is served with an `apply` verdict
(`PluginApplyStatus`: `installable`, `blockers`, `detail`, `active_run`),
computed on each read by `core.plugin_updates.apply.eligibility.apply_status`
and never cached. Blockers, in console order: `apply_disabled`,
`not_host_install`, `overlay_unconfigured`, `updater_offline` (no heartbeat
within three `heartbeat_seconds`), `updater_mismatch` (the updater runs another
core version), `unsigned_release`, `signature_failed`,
`needs_environment_update`, `host_build_required` and `run_active`. Only
`source`, `pip` and `custom` installs qualify (`custom` only names who wrote the
core-upgrade instructions, not the deployment's shape), and
`KUBERNETES_SERVICE_HOST` or a detected container blocks whatever
`SYSTEM_INSTALL_METHOD` says. A candidate
whose version the running tree already reached is served as not available.

A live updater that reports itself disabled, or without a restart command,
yields `apply_disabled` with a detail naming which. The verdict is re-stamped
on every read, including a "Check now" answered from the cooldown, so a run
started moments ago shows as `run_active` at once. Plugin names the run store
rejects (for example hyphenated ones) get no verdict (`apply` is `null`), so
they are never installable: the check fails closed.

### Overlay swap and store pruning

`core.plugin_updates.apply.swap` owns the plugin's link in the overlay.
`point_to` switches `<overlay>/<plugin>` to `.store/<plugin>-<version>` with a
temporary relative symlink, `os.replace` and a directory `fsync`, so a reader
sees the old or the new target and never neither; `None` unlinks (back to the
bundled copy). `current_target` reads the link and raises `ValueError` for a
plain directory or a link that leaves `.store`. `prune_store` removes the
oldest store entries of one plugin down to `UPDATE_APPLY_KEEP_VERSIONS`,
never the linked target or a name in `keep`, never follows a symlink, and
clears `.staging-*` / `.trash-*` scratch older than a day. It complements
`core.plugins.overlay_prune`, which removes entries the bundled plugin has
overtaken.

The link may be relative or absolute: `current_target` applies the loader's
rule (the resolved target must sit directly in `.store`), and additionally
requires the entry to be `<plugin>-<semantic version>`; a link to another
plugin's entry (or to `<plugin>-extra-1.0.0`), or one that cannot be resolved
(a symlink loop), raises
`ValueError`. When the link cannot be read, `prune_store` leaves every version
alone and only clears stale scratch.

### Staging a release

`core.plugin_updates.apply.staging.stage_release` turns the approved, cached
tarball into `.store/<plugin>-<version>/` plus the sidecar
`.store/<plugin>-<version>.release.json` (the signed `release.json`, read back
by `known_files_of`). Each step raises `StagingError` with a run failure code:

1. `release.json` must name this release, carry a valid signature from a
   trusted key and pin the same tarball SHA-256 the run was approved with
   (`verification_failed` / `artifact_checksum`).
2. `private_copy` opens the tarball **once** with `O_NOFOLLOW` and copies it
   through that descriptor into `.store/.staging-<run_id>/`, hashing the bytes
   as they are written; a link, a FIFO or a digest other than the pin is
   `artifact_checksum`. Everything after reads only that private copy, so a
   cached tarball replaced mid-run changes nothing.
3. The copy is unpacked with the checker's rules (member count, the
   `max_unpacked_bytes` bound, no links, no path escapes, a single plugin
   root) and verified with `verify_release` against the signed file list
   (`verification_failed`).
4. The installed tree must hold nothing but files its release shipped, its
   `.env` and its declared `runtime_state_paths` (`undeclared_runtime_state`).
   Shipped files come from the sidecar for a store entry, from `git ls-files`
   for a source checkout, and otherwise every file present counts as shipped
   (pip hosts: undeclared state cannot be detected there). An installed
   manifest whose `runtime_state_paths` cannot be read is `verification_failed`.
5. `.env` and the declared paths are copied into the new tree, never over a
   file the new release ships and never through a symbolic link; a link or a
   hash-covered file inside a carried path is refused (`overlay_refused`).
6. The result is re-verified as an overlay entry (`verify_overlay_entry`,
   then `overlay_refusal` against the bundled copy) — `overlay_refused`.
7. Promotion is one `os.rename` inside `.store`, then the sidecar is written
   (temp file + `os.replace`), so a sidecar never describes a missing entry;
   if the sidecar write fails the entry goes back into staging. An existing
   entry of the same name (with its sidecar) is never reused or overwritten:
   it is renamed into `.trash-<run_id>-*` (cleared later by `prune_store`),
   unless it is the entry the plugin's link currently points to, which refuses
   the run (`overlay_refused`). The caller holds the per-plugin run lock; the
   target is still re-checked right before the rename, because `os.rename`
   over an empty directory succeeds silently.

The staging directory is removed in every outcome, including read-only
directories a tarball created. `run_id` must match the run store's format
(`pinstall-<YYYYmmddTHHMMSSZ>-<8 hex>`), and error details name paths relative
to the overlay root (a bundled installed tree shows as `<installed>`).

### Boot report

After the plugins are up, every API worker calls
`core.plugin_updates.apply.boot_report.write_boot_report` (from
`core.api._runtime_services`, only when `UPDATE_APPLY_ENABLED`). It first
activates each plugin that has a pending expectation (a lazily loaded plugin
would otherwise never show up), then writes
`boot/<pid>.json` by atomic replace: the worker's pid, boot time, core
version, and per plugin its version, directory, `active` and `healthy` flags.
Nothing else is recorded. The directory is the resolved path: the loader
registers a plugin under its overlay link, so the report records the store
entry that link pointed to at boot, which is what the worker loaded and what
the health check compares with the run's target. Reports older than seven days are removed.
`read_boot_reports(store, since=...)` returns the reports of a restart and
`latest_active_plugins(store)` the active names of the newest one. The hook
logs and swallows every failure: a boot is never blocked or delayed by it.

`activation_timeout` (default 30 s) is one total deadline for all activations
and health probes; a health probe that times out reports `healthy=false`, and
plugins the budget did not reach are reported `active=false`, `healthy=null`.
The report is written regardless. A timed-out activation is cancelled mid-way
and can leave partial plugin state until the next restart. The hook adds a
five-second `asyncio.wait_for` margin as a backstop.

### Health check

`core.plugin_updates.apply.health.wait_healthy` decides whether a restart
succeeded. `evaluate_boot_reports` requires every worker that booted after
`restart_at` to show the target plugin active, on the expected version and
release directory (or off the overlay for a rollback to the bundled copy) with
`healthy` exactly `true` (`false` and unknown both fail), and every
`must_stay_active` plugin still active and not unhealthy. No report at all
fails. `wait_healthy` re-reads the reports on every poll and additionally needs
`health_url` to answer 200 for `stable_seconds` in a row within
`health_timeout_seconds`; a flapping probe or a late wrong worker resets the
window. Failure reasons name plugins and versions only, never host paths.

Each report also records `workers` (the launcher's declared worker count, 1
when unknown). The verdict fails with "N of M workers reported" until as many
distinct pids as the largest declared count have written a report since the
restart, so a worker that is still booting, crash-looping or never restarted
cannot be missed. The readiness probe itself is bounded by the overall
deadline.

### Executing a run

`core.plugin_updates.apply.executor.Executor` takes one approved run to a
terminal state; only the updater process calls it, and it never imports the
`plugins` package. An update:

1. moves `approved` → `preparing` (a compare-and-set: a run someone else
   moved is left alone) and refuses with `apply_disabled` when the kill switch
   is off, no restart command is configured, or **no boot report exists yet**
   (detail: "no boot report yet — restart the API once with
   UPDATE_APPLY_ENABLED on"; logged as a warning) — without a report nothing
   is known about which plugins must survive the restart, so the run fails
   closed. A report listing no active plugin is not the same thing and is
   accepted. All of this happens before touching anything;
2. reads `UPDATE_APPLY_SCHEMA_ENV_FILE` (the schema owner's credentials; never
   logged, never stored). When it is set but missing, unreadable, or group-
   or world-writable, the run fails `migration_failed` before anything is
   downloaded or staged — it never falls back to the runtime credentials;
3. fetches the signed `release.json` of the release tagged `v<version>` and
   holds it to the tarball SHA-256 pinned on the run when it was requested. A
   different name, version or tarball is `release_changed`; a bad signature or
   an unreachable release is `verification_failed`. The pin passed to
   `stage_release` is always the run's own, never one re-read from the
   download. A missing cached tarball is downloaded first (to a temporary
   file, then renamed). A tarball matching the pin whose manifest sets
   `host_build_required` is refused (`verification_failed`) before staging:
   the verdict already blocks such a release, and the updater re-checks it
   on what it is about to install;
4. stages the release (see above) in a worker thread **while holding the
   per-plugin run lock** (`RunStore.plugin_lock`), which also covers every
   link switch, the verification of a rollback target and the pruning;
5. records on the run, once, the plugins the newest boot report shows active
   (`must_stay_active`, the API as it ran before the run, read in step 1), journals
   `migrating` with the previous and new link targets, and runs
   `python -m core.cli plugin schema-init --plugin <name>` with the process
   environment — **minus** the names `PLUGIN_UPDATE_GITHUB_TOKEN`,
   `ADMIN_PASS`, `ADMIN_PASS_HASHED`, `METRICS_PASSWORD` and every
   `UPDATE_APPLY_*` variable, which a plugin's schema step has no use for —
   plus the owner's credentials and
   `BASELITH_PLUGIN_OVERLAY_DIR`
   pointed at a **scratch overlay**: `.store/.staging-<run>-schema-*/` holding
   `.store -> ..`, `<plugin> -> .store/<new entry>` and the other plugins'
   links as they are live. The live link is not touched: the running API
   resolves `plugins.<name>` through it for lazy imports, on-demand
   activation and respawned workers, so it must keep pointing at the code the
   API booted with for as long as `schema-init` runs (up to ten minutes). The
   scratch overlay is removed on every path. A non-zero exit fails the run
   `migration_failed` — "nothing changed on the running API", with no switch
   and no restart. Whatever schema-init changed before it failed stays in
   the database;
6. moves to `activating` and writes the restart expectation — its
   `restart_at` taken from the same UTC wall clock the workers stamp their
   boot reports with, its `must_stay_active` the list recorded in step 5 —
   then switches the live link, then issues `UPDATE_APPLY_RESTART_COMMAND`,
   moves to `health_checking` and waits for the health verdict. A worker
   respawned between the switch and the restart boots after `restart_at` on
   the new version, which is what the verdict expects. A switch that fails
   with the link still on the previous target fails the run
   `overlay_refused` without a restart;
7. on success journals `succeeded`, clears the expectation, and only then
   prunes the store (keeping the new and the previous target) and stale
   overlay links.

A failed restart (`restart_failed`) or verdict (`health_failed`) moves to
`rolling_back`: the link goes back to the previous target and the API is
restarted and judged again, against the same recorded `must_stay_active` (not
against the failed boot, which may already have lost dependents). Success ends
in `rolled_back`; failure ends in `rollback_failed`, logged at `CRITICAL`,
which keeps the plugin's claim until an operator intervenes. The recorded list
also serves `resume_activation` after a partial boot.

**A rollback never reverts `schema-init`.** The previous version runs against
the schema the new version created, so plugin schema changes must be
expand-only (additive, backward compatible) for at least one version. When
schema-init ran, the `rolled_back` detail says so: `schema changes from
<version> remain`.

A rollback run verifies its target store entry **before** the live link
changes: a scratch `.store/.staging-<run>-verify-*/` holds `.store -> ..` and
`<plugin> -> .store/<target>`, so the entry is judged under the plugin's own
name (signature, manifest name, core bounds, bundled version) exactly as the
loader would, and removed afterwards (the same scratch technique as
`schema-init`'s). A refused entry fails
`verification_failed` with the live link untouched; so does an I/O error
while building or walking the scratch entry (the detail names the error type,
never a path), and the claim is released. Only the structured
`not_newer` refusal (`core.plugins._overlay_guard.overlay_refusal_code`: the
bundled plugin has overtaken the entry) falls back to the bundled copy; an
unreadable bundled version is a refusal. A rollback run never runs
`schema-init`; like an update, it switches the live link only right before
its restart.

Every terminal state — including the up-front refusals and
`rollback_failed` — is journaled first and then clears the plugin's restart
expectation, **only when that expectation is the run's own** (its `run_id`
matches), so a later run's expectation is never deleted; a crash in between leaves an expectation of a finished run, which
`RunStore.clear_stale_expectations()` (called by reconciliation) removes. Run
messages carry failure codes, versions, store entry names and exit codes,
never host paths, URLs or credentials; a boot report's `directory` is never
copied into a run. All blocking work (the run store, staging, link switches,
pruning, commands, boot-report reads) runs in worker threads.
`resume_activation`, `redo_rollback` and `undo_swap` are the reconciliation
entry points for an updater that died mid-run.

`core.plugin_updates.apply._io` holds the seams: `subprocess_runner` (an argv
list, `shell=False`, the exit code or `-1` on timeout / `OSError`; only the
program name is logged), `schema_env` (raises `SchemaEnvError`, without the
path, for a configured file it cannot read), `GitHubReleaseFetcher` (one
GitHub API lookup of the `v<version>` release tag for the repository slug, through
`GitHubReleaseSource.release_by_tag`, then the assets) and `http_probe` (a
5 s GET of `health_url`; `0` when unreachable).

### Crash reconciliation

`core.plugin_updates.apply.reconcile.reconcile(store, executor)` brings every
run a dead updater left behind to a terminal state. It expires
stale approval requests first, then, per unfinished run:

| State found | Action |
| --- | --- |
| `approved`, `awaiting_approval` (not yet stale), `rollback_failed` | untouched |
| `preparing` | nothing was switched: its `.store/.staging-<run>*` directories are removed, `failed: interrupted` |
| `migrating` | `schema-init` ran (or was running) against a scratch overlay and the live link was never switched: its scratch and staging directories are removed, `failed: interrupted` ("nothing changed on the running API"). `Executor.undo_swap` only acts on a link an older updater had already switched, putting it back on `previous_target`; one that cannot be switched back is `rollback_failed` (CRITICAL log) |
| `activating`, `health_checking` | `Executor.resume_activation`: the updater died before or after switching the link, so it switches (idempotently) and restarts again, then judges it; pass → `succeeded`, fail → rollback. `schema-init` is not run again |
| `rolling_back` | `Executor.redo_rollback` (idempotent) |

It ends with `RunStore.clear_stale_expectations()`. Each handled run is logged
as `AUDIT | PLUGIN_UPDATE | reconciled`. When one run's recovery raises, the
remaining runs are still settled and stale expectations cleared before the
first error is re-raised. A run recorded before `must_stay_active` existed
that is resumed with no boot report at all is never judged healthy: it is
rolled back (`health_failed`, "no boot report yet …").

### The updater service

`baselith plugin-updater serve` (`core.plugin_updates.apply.updater.serve`)
is a separate host process: it never serves HTTP, never imports the
`plugins` package (it refuses to run if plugin code was loaded, and the CLI
skips its plugin-CLI scan for this command) and never starts Sentry or any
other error reporter — `schema-init`'s environment, which holds the schema
owner's credentials, is a local of the executor, and a reporter capturing
frame locals would ship it off-host. Unexpected errors are logged by type name
only, never with a traceback.

It takes an exclusive `flock` on `<state_dir>/updater.lock` (a second updater
exits `2`; the fd is not inheritable, and subprocesses start with
`close_fds`), publishes `heartbeat.json` every `UPDATE_APPLY_HEARTBEAT_SECONDS`,
reconciles, then loops: prune old finished runs (at start, then at most once
an hour: `RunStore.prune_finished`; a failure is logged and ignored), expire
stale approval requests, execute the oldest approved run, or wait
`UPDATE_APPLY_POLL_SECONDS`. Every store call runs in a
worker thread. SIGTERM/SIGINT stop it between runs; the heartbeat has its own
stop and keeps beating while a run in flight finishes (an error writing it is
logged by type name and the next beat is tried). A run still in flight when
systemd's stop timeout expires is reconciled at the next start.

**Crash policy.** An executor call that raises unexpectedly is reconciled at
once, so the run never stays stranded holding its plugin's claim, and the loop
goes on. When the run is still unfinished after that (the executor failed
before it left `approved`), `serve` exits non-zero: systemd restarts it
(`Restart=on-failure`), the next start reconciles again, and a repeating
failure trips the unit's start limit, leaving it `failed` rather than looping.

**`plugin.update_rollback_failed`.** A run that ends `rollback_failed`
(from a run, a resumed activation, a redone rollback or a failed undo) emits
`plugin.update_rollback_failed` (payload: `run_id`, `plugin`, `kind`,
`from_version`, `to_version`, `failure`) on the updater's in-process event bus,
next to the CRITICAL `AUDIT | PLUGIN_UPDATE | rollback_failed` log line. That
bus is not shared with the API workers, so subscribers in the API see the
event only when the web tier re-announces the outcome it audits once per run.

The event name and payload builder are public:
`core.plugin_updates.apply.ROLLBACK_FAILED_EVENT` and
`core.plugin_updates.apply.rollback_failed_payload(run)`, so a web tier that
records the outcome re-announces it with the same payload.

`build_executor(config)` wires the production executor: the overlay from
`BASELITH_PLUGIN_OVERLAY_DIR` (unset → exit `2`), the bundled root from
`PLUGINS_PATH`, the release sources, GitHub API and download cap from the
`PLUGIN_UPDATE_*` settings (the sources file is read once at start), trusted
keys re-read for every run, `http_probe(UPDATE_APPLY_HEALTH_URL)` and
`subprocess_runner`.

### systemd units

A unit for a virtualenv layout runs `baselith plugin-updater serve`. It is deliberately **not** `PartOf`,
`BindsTo` or `Requires` the API unit: the updater restarts the API and must
survive that restart to health-check it and roll back. `Restart=on-failure`
with `StartLimitBurst=5` in 600 s makes a crash loop end `failed`;
`RestartPreventExitStatus=2` keeps a configuration refusal from looping at
all. The unit grants no privilege: the only privileged command is the API
restart, allowed by a narrow sudoers rule
(`<user> ALL=(root) NOPASSWD: /usr/bin/systemctl restart baselithcore.service`)
and configured as
`UPDATE_APPLY_RESTART_COMMAND=["sudo","-n","/usr/bin/systemctl","restart","baselithcore.service"]`.
Because sudo is setuid, `NoNewPrivileges=true` — and the sandboxing options
that imply it for a non-root unit (`ProtectKernel*`, `RestrictSUIDSGID`,
`SystemCallFilter`, …) — would break it and are left off; turn them on if you
grant the restart through a polkit rule instead.

Adjust paths and user to your host (save it as
`/etc/systemd/system/baselithcore-plugin-updater.service`):

```ini
[Unit]
Description=BaselithCore — plugin updater (one-click plugin updates)
After=network-online.target baselithcore.service
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=simple
User=baselith
Group=baselith
WorkingDirectory=/opt/baselithcore
EnvironmentFile=/etc/baselithcore/env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/baselithcore/.venv/bin/baselith plugin-updater serve
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=2
KillSignal=SIGTERM
TimeoutStopSec=30
PrivateTmp=true
ProtectHome=true
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```
