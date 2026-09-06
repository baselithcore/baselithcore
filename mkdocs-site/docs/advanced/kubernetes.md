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
| Autoscaling | `HorizontalPodAutoscaler` (CPU + memory targets) |
| Disruption safety | `PodDisruptionBudget` (`minAvailable`) |
| Liveness | `GET /health` (process-up only) |
| Readiness | `GET /health/ready` → **503 when the DB is unreachable**, so traffic drains |
| Graceful shutdown | `terminationGracePeriodSeconds` + `preStop` sleep, pairs with the app's `GracefulShutdown` handler |
| Pod hardening | non-root (uid 1000), read-only rootfs, all caps dropped, `RuntimeDefault` seccomp |
| SA token | `automountServiceAccountToken: false` on the ServiceAccount and both pod specs — the app never calls the Kubernetes API, so no pod carries a projected token (`serviceAccount.automountToken` to opt back in) |
| Spread | `topologySpreadConstraints` across nodes |
| Config / secrets | `ConfigMap` (non-secret) + `Secret` (chart-managed or external) via `envFrom`. The pre-deploy migration hook gets its own hook-scoped copies (`-migrate-config` / `-migrate-secrets`, hook weight `-5`, deleted once the Job succeeds): Helm applies ordinary resources *after* its hooks, so a Job reading the release ConfigMap could never start on a first install |
| Host validation | `TRUSTED_HOSTS` is derived from `ingress.hosts` plus the in-cluster Service names — `APP_ENV=production` with an empty list is a hard startup abort. The probes carry a matching `Host` header (`probeHost`, defaulting to the Service FQDN), because `TrustedHostMiddleware` answers **400** to the kubelet, which addresses the pod by IP. Setting `config.TRUSTED_HOSTS` by hand therefore also requires setting `probeHost`; the chart refuses to render otherwise |
| Writable paths | `readOnlyRootFilesystem: true` leaves only the `/tmp` mount writable. Anything persisting inside the image tree — a plugin's data directory, a Hugging Face cache under `/app/models` — needs `extraVolumes` / `extraVolumeMounts`, or it fails at boot |
| Metrics | optional `ServiceMonitor` scraping `/metrics`. Prometheus addresses each pod **by IP** and a `ServiceMonitor` endpoint has no field for a `Host` header, so the scrape would arrive as `Host: <pod-ip>` and `TrustedHostMiddleware` would answer **400** — a target permanently down with nothing but 400s in the app log. `serviceMonitor.trustPodIP` (default on) adds the pod's own IP to `TRUSTED_HOSTS` through the downward API. `/metrics` is admin-basic-auth-protected by default: point `serviceMonitor.basicAuth.secretName` at a Secret holding `ADMIN_USER` / `ADMIN_PASS`, or turn `METRICS_AUTH_REQUIRED` off and restrict the endpoint with a `NetworkPolicy` |
| Resources | `requests.memory` **1536Mi**, `limits.memory` 3Gi, and **no CPU limit** — see *Sizing* below |
| Network | optional `NetworkPolicy` (`networkPolicy.enabled`). Ingress defaults to any pod in the namespace; narrow it with `networkPolicy.ingressFrom`. **Egress is the half that matters for an agent runtime** and is a separate opt-in (`networkPolicy.egress.enabled`): with outbound unrestricted, a prompt-injected agent or a hostile tool result reaches whatever the pod network routes to, cloud metadata included. Enabling it is deny-by-default outbound, so list what the pod legitimately needs in `networkPolicy.egress.rules` (DNS is kept open separately); `values.yaml` carries a worked example |
| Workers | optional `core.task_queue` worker `Deployment` running `baselith queue worker`. It ships **no** liveness probe: an RQ worker that hangs mid-job keeps its process alive, so the kubelet never restarts it and the queue stops draining silently. Supply one through `worker.livenessProbe` (rendered verbatim; `values.yaml` carries a working candidate based on the RQ heartbeat registry, commented out) after validating it against your own deployment — a probe that cannot reach Redis restarts healthy workers |

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
    `.Chart.AppVersion` (`0.30.0`). Override both with `--set` when you
    mirror the image into a private registry.

## Supply chain: signed images & provenance

Release images (`linux/amd64` + `linux/arm64`, built from `Dockerfile-full`)
are pushed to GHCR, **signed with cosign** (keyless, Sigstore OIDC), scanned
with Trivy, and carry two kinds of attestation
(`.github/workflows/release-image.yml`):

- **BuildKit attestations** — SLSA provenance (`provenance: mode=max`) and an
  SBOM (`sbom: true`) attached to the image index at push time.
- **GitHub artifact attestation** — build provenance generated by
  `actions/attest-build-provenance` and pushed to the registry next to the image.

Verify before deploying:

```bash
IMAGE=ghcr.io/baselithcore/baselithcore:0.30.0

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
