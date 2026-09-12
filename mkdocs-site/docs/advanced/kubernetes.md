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
| Resources | `requests.memory` **1536Mi**, `limits.memory` 3Gi, and **no CPU limit** — see *Sizing* below |
| Network | optional `NetworkPolicy` (`networkPolicy.enabled`). Ingress defaults to any pod in the namespace; narrow it with `networkPolicy.ingressFrom`. **Egress is the half that matters for an agent runtime** and is a separate opt-in (`networkPolicy.egress.enabled`): with outbound unrestricted, a prompt-injected agent or a hostile tool result reaches whatever the pod network routes to, cloud metadata included. Enabling it is deny-by-default outbound, which is why it stays off everywhere — so the chart ships the rules a standard install needs as presets: `sameNamespace` (the in-cluster datastores) and `internetHttps` (TCP/443 out, with cloud metadata `169.254.0.0/16` and the RFC1918 / CGNAT / loopback ranges carved out). Datastores *outside* the cluster live in those carved-out ranges and need their own entry under `networkPolicy.egress.rules`. Note that `ingressFrom: []` means "same namespace only", which locks out both an ingress controller and a Prometheus living elsewhere — name them, or the deployment goes dark the moment you turn the policy on |
| Workers | optional `core.task_queue` worker `Deployment` running `baselith queue worker`. It ships **no** liveness probe: an RQ worker that hangs mid-job keeps its process alive, so the kubelet never restarts it and the queue stops draining silently. Supply one through `worker.livenessProbe` (rendered verbatim; `values.yaml` carries a working candidate based on the RQ heartbeat registry, commented out) after validating it against your own deployment — a probe that cannot reach Redis restarts healthy workers. It gets its **own** `terminationGracePeriodSeconds` (120s, not the API's 45s): RQ answers SIGTERM with a warm shutdown that finishes the job in flight, and the API's HTTP-drain budget would SIGKILL it mid-job. Its rollout uses `maxSurge: 0` — a queue consumer gains nothing from an extra pod and surging doubles a multi-GB footprint |
| Worker autoscaling | optional KEDA `ScaledObject` (`worker.keda`) driving an HPA from RQ queue depth. CPU cannot see a backlog that lives in Redis, so a worker with a thousand jobs waiting looks idle to a CPU-based HPA. The Redis address must be **fully qualified** — KEDA connects from its own namespace, and a short Service name leaves the ScaledObject `Ready=False` with "no such host" while the worker silently never scales |
| Values validation | `values.schema.json` — Helm validates the merged values on install, upgrade, template and lint. Unknown top-level keys are rejected (a silently ignored typo is the classic way a setting "does not work"), quantities must parse, enums must be spelled right, and `image.digest` must be a full `sha256:` |
| Smoke test | `helm test <release>` runs a pod that requests `/health/ready` through the Service, retrying past a cold start. A successful `helm install` only means the API server accepted the objects; this is the part that says a request gets an answer |

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

!!! warning "Image publication is opt-in — a release does not build one"
    The image job is **disabled by default**: `ci.yml` runs it only when the
    repository variable `RELEASE_IMAGE_ENABLED` is set to `true`. So a new
    version on PyPI does **not** imply a matching tag on GHCR, and the tag the
    chart defaults to may not exist for the version you are deploying. Check
    before you rely on it, and pin `image.tag` to a tag you have verified:

    ```bash
    docker manifest inspect ghcr.io/baselithcore/baselithcore:<version>
    ```

    To cut one on demand, run the **Release Container Image** workflow from the
    Actions tab (`workflow_dispatch`) with the git tag, e.g. `v0.31.0`.

When it does run, release images (`linux/amd64` + `linux/arm64`, built from
`Dockerfile-full`) are pushed to GHCR, **signed with cosign** (keyless,
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
