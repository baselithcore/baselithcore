# Kubernetes Deployment (Helm)

<!-- markdownlint-disable-file MD046 -->

A production-grade Helm chart and a cloud-agnostic Terraform module live under
[`deploy/`](https://github.com/baselithcore):

```text
deploy/helm/baselithcore/    # Helm chart
deploy/terraform/            # Terraform module (deploys the chart)
```

## What the chart provides

| Concern | Implementation |
|---|---|
| Rolling updates | `maxUnavailable: 0`, `maxSurge: 1` (zero-downtime) |
| Autoscaling | `HorizontalPodAutoscaler` on **CPU only**, with a `behavior` block. The memory target is off by default and enabling it is usually a mistake: HPA measures usage/request, and this pod's memory is dominated by a constant (resident model weights) that does not shrink per replica — so a correctly sized pod reads as permanently over target and the deployment parks at `maxReplicas` forever. `behavior` adds two pods per minute at most and removes one per three minutes after a 10-minute window, because each pod costs a multi-GB cold start to create and a warm model cache to destroy |
| Disruption safety | `PodDisruptionBudget` for the API (and optionally the worker), with `unhealthyPodEvictionPolicy: AlwaysAllow` so a pod that is already broken cannot hold a node drain hostage. The chart **refuses to render** a budget that can never be satisfied — `minAvailable: 1` against a single replica denies every eviction and hangs drains until somebody finds the PDB at 3am |
| Liveness | `GET /health` (process-up only) |
| Readiness | `GET /health/ready` → **503 when the DB is unreachable**, so traffic drains |
| Graceful shutdown | `terminationGracePeriodSeconds` + `preStop` sleep, pairs with the app's `GracefulShutdown` handler |
| Pod hardening | non-root (uid 1000), read-only rootfs, all caps dropped, `RuntimeDefault` seccomp — which is the Pod Security Standards **`restricted`** profile. Satisfying it and having it *enforced* are different claims: enforcement is a label on the namespace, which the chart deliberately does not own (a chart that creates its own namespace fights `helm --create-namespace` and every GitOps tool). Label it where the namespace is created — `pod-security.kubernetes.io/enforce=restricted` plus `audit`/`warn` — and remember that anything else sharing that namespace has to comply too |
| SA token | `automountServiceAccountToken: false` on the ServiceAccount and both pod specs — the app never calls the Kubernetes API, so no pod carries a projected token (`serviceAccount.automountToken` to opt back in) |
| Spread | `topologySpreadConstraints` over **both** `kubernetes.io/hostname` and `topology.kubernetes.io/zone` (`zoneAware`, on by default and free on a single-zone cluster), for the API and — with more than one replica — the worker. Each constraint carries `matchLabelKeys: [pod-template-hash]`, so the outgoing ReplicaSet does not count towards the incoming one's skew and a rollout cannot wedge itself against its predecessor |
| Config / secrets | `ConfigMap` (non-secret) + `Secret` (chart-managed or external) via `envFrom`. The pre-deploy migration hook gets its own hook-scoped copies (`-migrate-config` / `-migrate-secrets`, hook weight `-5`, deleted once the Job succeeds): Helm applies ordinary resources *after* its hooks, so a Job reading the release ConfigMap could never start on a first install |
| Host validation | `TRUSTED_HOSTS` is derived from `ingress.hosts` plus the in-cluster Service names — `APP_ENV=production` with an empty list is a hard startup abort. The probes carry a matching `Host` header (`probeHost`, defaulting to the Service FQDN), because `TrustedHostMiddleware` answers **400** to the kubelet, which addresses the pod by IP. Setting `config.TRUSTED_HOSTS` by hand therefore also requires setting `probeHost`; the chart refuses to render otherwise |
| Writable paths | `readOnlyRootFilesystem: true` leaves only the `/tmp` mount writable. Anything persisting inside the image tree — a plugin's data directory, a Hugging Face cache under `/app/models` — needs `extraVolumes` / `extraVolumeMounts`, or it fails at boot. A file the app *rewrites* needs one more step: `configs/plugins.yaml` is rewritten on every plugin enable/disable, so it has to move onto a volume (`config.PLUGIN_CONFIG_PATH`, which must resolve inside `/app`) — and a fresh volume is empty, which would boot every plugin unconfigured. `seedFromImage` runs an initContainer that copies each `from` (image path) to its `to` (a path under one of `extraVolumeMounts`) exactly once, never overwriting a destination that already exists, so what the deployment wrote survives restarts and image bumps. On more than one api replica point it at a **ReadWriteMany** volume: `plugins.yaml` is per-filesystem, so a toggle applied on one pod is invisible to the others |
| Plugin set | the opposite stance, for deployments where the plugin set is a property of the release rather than a runtime toggle: `plugins.config` is the `plugins.yaml` content (plugin name → its block) and, when non-empty, is rendered into a ConfigMap and mounted **read-only** over `/app/configs/plugins.yaml` in the api and worker pods. No `PLUGIN_CONFIG_PATH`, no volume, no seed; the pods roll whenever the content changes, and every console or CLI toggle fails with `Read-only file system` — deliberately, so the running state can never drift from the manifest. This is what a per-customer SaaS cell uses (see `deploy/customers/`). Remember the loader's rule: when the file has entries, a plugin absent from it is not loaded, so list every plugin the release needs, system plugins included. Mutually exclusive with `config.PLUGIN_CONFIG_PATH` and with a `seedFromImage` entry from `/app/configs/plugins.yaml`; the chart refuses to render the combination |
| Metrics | optional `ServiceMonitor` scraping `/metrics`. Prometheus addresses each pod **by IP** and a `ServiceMonitor` endpoint has no field for a `Host` header, so the scrape would arrive as `Host: <pod-ip>` and `TrustedHostMiddleware` would answer **400** — a target permanently down with nothing but 400s in the app log. `serviceMonitor.trustPodIP` (default on) adds the pod's own IP to `TRUSTED_HOSTS` through the downward API. `/metrics` is admin-basic-auth-protected by default: point `serviceMonitor.basicAuth.secretName` at a Secret holding `ADMIN_USER` / `ADMIN_PASS`, or turn `METRICS_AUTH_REQUIRED` off and restrict the endpoint with a `NetworkPolicy` |
| Alerts | optional `PrometheusRule` (`prometheusRule.enabled`) with eleven rules over the RED HTTP metrics the app already exports, `mas_llm_*`, `security_events_total`, kube-state-metrics and the kubelet's volume stats. It does not replace `deploy/prometheus/*.yml` — those are file-based rules for the single-host Prometheus the compose stack runs, and most of what is here could not exist there (nothing in a compose deployment knows a Deployment's replica count, a hook Job's exit status or a PVC filling up). Where they overlap, the error-rate rule reads the same `http_requests_total` series `slo-rules.yml` uses as its availability SLI, so alert and SLO cannot drift apart. Two details decide whether they are worth having. **Every "nothing is running" rule is qualified by `kube_deployment_spec_replicas > 0`**: a suspended cell *is* zero replicas with the data kept, and KEDA may scale the worker to zero on an empty queue — both are desired states indistinguishable from an outage, and without the guard every suspension pages somebody for a cell that is off on purpose. The error rules are **ratios with a traffic guard**, because an absolute threshold is either useless on a cell serving ten requests a minute or deaf on one serving ten thousand, and a ratio over a zero denominator is NaN, which compares false and would switch the rule off exactly when the cell is quiet. Thresholds are values, not constants (`prometheusRule.thresholds`); `disabledAlerts` drops one by name, which keeps the reason in git where an Alertmanager silence does not. `prometheusRule.labels` is usually what decides whether anything is evaluated at all — kube-prometheus-stack selects rule objects by label (commonly `release: <stack release>`), so an object without it is created, accepted and ignored |
| Resources | `requests.memory` **1536Mi**, `limits.memory` 3Gi, and **no CPU limit** — see *Sizing* below |
| Network | optional `NetworkPolicy` (`networkPolicy.enabled`). Ingress defaults to any pod in the namespace; narrow it with `networkPolicy.ingressFrom`. **Egress is the half that matters for an agent runtime** and is a separate opt-in (`networkPolicy.egress.enabled`): with outbound unrestricted, a prompt-injected agent or a hostile tool result reaches whatever the pod network routes to, cloud metadata included. Enabling it is deny-by-default outbound, which is why it stays off everywhere — so the chart ships the rules a standard install needs as presets: `sameNamespace` (the in-cluster datastores) and `internetHttps` (TCP/443 out, with cloud metadata `169.254.0.0/16` and the RFC1918 / CGNAT / loopback ranges carved out). Datastores *outside* the cluster live in those carved-out ranges and need their own entry under `networkPolicy.egress.rules`. Note that `ingressFrom: []` means "same namespace only", which locks out both an ingress controller and a Prometheus living elsewhere — so the chart **refuses to render** a policy with an empty `ingressFrom` while `ingress.enabled` or `serviceMonitor.enabled` is on, and `values-production.yaml` names the `ingress-nginx` and `monitoring` namespaces |
| Workers | optional `core.task_queue` worker `Deployment` running `baselith queue worker`. It ships **no** liveness probe: an RQ worker that hangs mid-job keeps its process alive, so the kubelet never restarts it and the queue stops draining silently. Supply one through `worker.livenessProbe` (rendered verbatim; `values.yaml` carries a working candidate based on the RQ heartbeat registry, commented out) after validating it against your own deployment — a probe that cannot reach Redis restarts healthy workers. It gets its **own** `terminationGracePeriodSeconds` (120s, not the API's 45s): RQ answers SIGTERM with a warm shutdown that finishes the job in flight, and the API's HTTP-drain budget would SIGKILL it mid-job. Its rollout uses `maxSurge: 0` — a queue consumer gains nothing from an extra pod and surging doubles a multi-GB footprint |
| Worker autoscaling | optional KEDA `ScaledObject` (`worker.keda`) driving an HPA from RQ queue depth. CPU cannot see a backlog that lives in Redis, so a worker with a thousand jobs waiting looks idle to a CPU-based HPA. The Redis address must be **fully qualified** — KEDA connects from its own namespace, and a short Service name leaves the ScaledObject `Ready=False` with "no such host" while the worker silently never scales |
| Values validation | `values.schema.json` — Helm validates the merged values on install, upgrade, template and lint. Unknown top-level keys are rejected (a silently ignored typo is the classic way a setting "does not work"), quantities must parse, enums must be spelled right, and `image.digest` must be a full `sha256:` |
| Smoke test | `helm test <release>` runs a pod that requests `/health/ready` through the Service, retrying past a cold start. A successful `helm install` only means the API server accepted the objects; this is the part that says a request gets an answer |
| Backups | `backup.enabled` runs a nightly `pg_dump` CronJob (it waits for the database first: a CronJob replays missed schedules on a cluster that just came back, ahead of CoreDNS). Alone it writes to `backup.volume` — an emptyDir by default, a file on the node that just died. `backup.offsite` makes it a backup: the dump becomes an init container and an rclone container copies each `backup_*.sql.gz` to any rclone backend (`remote` keys become `RCLONE_CONFIG_OFFSITE_<KEY>`: S3 through IRSA, GCS through Workload Identity, Azure Blob, an S3-compatible store with `provider: Other` + `endpoint`; static keys via `credentialsSecret`). The backup gets its own ServiceAccount (`backup.serviceAccount`) so the bucket role is granted to it alone, never to the serving pods. `copy`, never `sync`: the remote keeps its own history, pruned by `offsite.retentionDays` or by the bucket's lifecycle rules |
| Streaming through the ingress | `values-production.yaml` sets `nginx.ingress.kubernetes.io/proxy-buffering: "off"`, which is only half of what a stream needs: ingress-nginx also applies **`proxy-read-timeout`, default 60s**, so a stream idle for a minute — an agent waiting on a slow LLM call — was cut by the ingress with buffering already disabled. The overlay now sets `proxy-read-timeout` / `proxy-send-timeout` to `300`, and because its single `/` prefix rule routes everything, that covers every streaming path the app serves (`/chat/stream` and its `/v1` alias, `/runs/{id}/events`, `/mcp`). A different controller needs the same two knobs under its own annotation names |
| Image tag | `image.tag` is empty by default, so **`Chart.appVersion` is the tag a plain install pulls** — not documentation. It had drifted a release behind the code (`0.31.0` against a `0.32.0` tree) because semantic-release rewrote only `core/_version.py`, so the chart quietly deployed the previous image; `.releaserc`'s `prepareCmd` now rewrites `Chart.yaml` alongside `core/_version.py`, so the checked-in pair moves together in the release-prep pull request, and `tests/unit/test_helm_chart.py` fails if the two disagree. For a real pin use `image.digest`: a tag is a mutable pointer, so the image an admission controller verified and the one that starts weeks later after a re-push need not be the same bytes. Note that the container-image release job is **opt-in** (`RELEASE_IMAGE_ENABLED`), so confirm the tag exists in the registry before rolling a cluster onto it |
| Chart validation | `helm template` over the default values, the production overlay and every optional branch (backup `CronJob`, KEDA `ScaledObject`, chart-managed `Secret`, multi-worker metrics wiring) runs in the ordinary test job — GitHub's ubuntu runner ships helm, so no separate job or pinned action is involved. Before this, nothing in CI rendered the chart at all: Trivy's misconfig scan is report-only and cannot expand Go templates, so a tripped `fail` guard or a schema-rejected key surfaced at `helm install` time, in the cluster |

## Quick start

```bash
helm upgrade --install baselithcore deploy/helm/baselithcore \
  -n baselithcore --create-namespace \
  -f deploy/helm/baselithcore/values-production.yaml \
  --set-string secrets.existingSecret=baselithcore-secrets
```

`values-production.yaml` is a ready-to-edit overlay (ingress, TLS via
cert-manager, HPA 3–20, workers, ServiceMonitor, NetworkPolicy).

!!! warning "Sizing"
    **Measure, do not guess — and never measure an idle pod.** The defaults
    (`requests.memory: 1536Mi`, `limits.memory: 3Gi`) come from watching core's
    own document-ingestion job, which takes an RQ worker from 84Mi idle to
    1.1-1.45Gi. Give `/app/models` a volume and the plugins that own an embedder
    download and load real models at boot — several GB more — and a pod sized
    for the idle reading is OOMKilled mid-startup.

    `requests.memory` is not decoration: it is what the scheduler promises,
    *and* the baseline the kernel's OOM killer scores a cgroup against — a
    container is chosen by how far it exceeds its request, so understating it
    both overpacks the node and moves that pod to the front of the queue.
    Set the request on steady-state usage and the limit above the peak, which
    for a model-loading pod is the cold start rather than the traffic.

    **There is no `limits.cpu`, on purpose.** CPU is compressible: a cap cannot
    prevent exhaustion, it only throttles — and the worst moment to be
    throttled is the cold start an autoscaler just triggered. Contention is
    already settled by `requests.cpu`, which sets the cgroup weight. Add a CPU
    limit back only when a hard ceiling is a billing or noisy-neighbour
    requirement, and never below the cold-start burst.

!!! note "Image name"
    `values.yaml` and `values-production.yaml` default `image.repository` to
    `ghcr.io/baselithcore/baselithcore`, the name the release workflow
    publishes under (`IMAGE_NAME: ${{ github.repository }}` in
    `.github/workflows/release-image.yml`); `image.tag` defaults to
    `.Chart.AppVersion` (`0.31.0`). Override both with `--set` when you
    mirror the image into a private registry.

    Each release publishes four tags for the same index: the exact version
    (`0.33.0`), the minor (`0.33`), the major (`0`) and `latest`. The moving
    ones let a deployment track patches or minors without a chart edit per
    release; a prerelease (`1.0.0-rc.1`) gets its exact tag only and never
    moves `latest`. For production, prefer `image.digest` over any of them —
    see below.

!!! warning "First publish: check the package is public"
    A GHCR package created by a workflow is not guaranteed to be world-readable,
    and the chart pulls anonymously — `pullSecrets` is empty by default. After
    the first release that pushes an image, confirm an unauthenticated pull
    works before relying on the defaults:

    ```bash
    docker logout ghcr.io && docker pull ghcr.io/baselithcore/baselithcore:0.31.0
    ```

    A `denied` / `401` means the package is still private: make it public under
    the org's package settings, or set `pullSecrets` in your values.

!!! tip "Pin the image by digest"
    `image.digest` (`sha256:…`) replaces the tag in the pod spec. A tag is a
    mutable pointer: the image an admission controller verified and the image
    that starts three weeks later after a re-push under the same tag are not
    necessarily the same bytes, and nothing in the cluster notices. Pin the
    digest your release pipeline signed and verify it below; the tag then
    survives only as documentation.

## Supply chain: signed images & provenance

!!! info "Every release publishes an image"
    The image job runs on every release. It was opt-in for a while, behind a
    `RELEASE_IMAGE_ENABLED` variable that was never set — so the versions
    released in that period have no matching tag on GHCR. For those, cut one on
    demand by running the **Release Container Image** workflow from the Actions
    tab (`workflow_dispatch`) with the git tag, e.g. `v0.31.0`.

    `RELEASE_IMAGE_DISABLED=true` is the kill switch if you need to stop
    publishing without editing a workflow.

Release images (`linux/amd64` + `linux/arm64`, built from the repository's
single `Dockerfile`) are pushed to GHCR, **signed with cosign** (keyless,
Sigstore OIDC), scanned with Trivy, and carry two kinds of attestation
(`.github/workflows/release-image.yml`):

- **BuildKit attestations** — SLSA provenance (`provenance: mode=max`) and an
  SBOM (`sbom: true`) attached to the image index at push time.
- **GitHub artifact attestation** — build provenance generated by
  `actions/attest-build-provenance` and pushed to the registry next to the image.

Verify before deploying:

```bash
IMAGE=ghcr.io/baselithcore/baselithcore:0.31.0

# 1. Cosign signature (keyless — issued via GitHub Actions OIDC)
cosign verify "$IMAGE" \
  --certificate-identity-regexp "https://github.com/baselithcore/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"

# 2. GitHub artifact attestation (SLSA provenance, verified against the repo)
gh attestation verify "oci://$IMAGE" --repo baselithcore/baselithcore

# 3. BuildKit provenance / SBOM attached to the image index
docker buildx imagetools inspect "$IMAGE" --format '{{ json .Provenance }}'
docker buildx imagetools inspect "$IMAGE" --format '{{ json .SBOM }}'
```

Enforce signatures at admission with a policy controller (Sigstore policy-controller
or Kyverno) so only signed images run in the cluster.

## Secrets

Two options (the chart never requires plaintext in `values.yaml`):

1. **External (recommended).** Create a `Secret` with External Secrets
   Operator, Vault Agent, or sealed-secrets, then set
   `secrets.create=false` and `secrets.existingSecret=<name>`.
2. **Chart-managed.** Set `secrets.create=true` and pass values with
   `--set-string secrets.data.SECRET_KEY=...`.

Required keys: `SECRET_KEY`, plus any of `DATA_ENCRYPTION_KEYS`, `DB_PASSWORD`,
`ANTHROPIC_API_KEY`, etc. See
[Security & Encryption](../core-modules/security.md) for the encryption keys
and the `file` secrets backend (mount K8s secrets and set `SECRETS_BACKEND=file`).

## Probes & draining

The readiness endpoint distinguishes *being alive* from *being able to serve*:
when Postgres is down it returns 503, Kubernetes removes the pod from the
Service endpoints, and the liveness probe keeps it from being killed so it can
recover. Redis is reported but advisory (the framework falls back to in-memory),
so it does not gate readiness.

## Terraform

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # edit; keep out of git
terraform init
terraform apply
```

The module creates the namespace, renders sensitive values into a
Terraform-managed `Secret` (consumed via `secrets.existingSecret` so they never
appear in the Helm release manifest), and installs the chart. Providers are
pinned to `kubernetes ~> 2.27` and `helm ~> 2.13`.
