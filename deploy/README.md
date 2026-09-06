# Deploy

Production deployment assets for BaselithCore.

| Path | Purpose |
|---|---|
| `helm/baselithcore/` | Production-grade Helm chart (Deployment, HPA, PDB, Service, Ingress, ServiceAccount, ServiceMonitor, NetworkPolicy, optional worker). |
| `terraform/` | Cloud-agnostic Terraform module that installs the chart into an existing cluster and manages the namespace + a credentials Secret. |
| `nginx/` | Reverse-proxy config (SSE-friendly buffering). |
| `prometheus/` | Alert rules. |
| `sandbox/` | Sandbox runtime config. |

See [docs: Kubernetes (Helm)](../mkdocs-site/docs/advanced/kubernetes.md) for the
full guide.

## TL;DR

```bash
# Helm (chart-managed secret)
helm upgrade --install baselithcore helm/baselithcore \
  -n baselithcore --create-namespace \
  -f helm/baselithcore/values-production.yaml \
  --set-string secrets.create=true \
  --set-string secrets.data.SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(64))')"

# Terraform
cd terraform && cp terraform.tfvars.example terraform.tfvars  # edit, gitignored
terraform init && terraform apply
```

## Probes

- Liveness: `GET /health` — process up.
- Readiness: `GET /health/ready` — returns 503 when Postgres is unreachable so
  Kubernetes drains traffic; Redis is advisory.

All three probes carry a `Host` header (`probeHost`, defaulting to the Service
FQDN). `TRUSTED_HOSTS` mounts Starlette's `TrustedHostMiddleware`, and the
kubelet addresses the pod by IP: without the header every probe gets a 400 and
the pod never becomes ready. Set `probeHost` yourself whenever you set
`config.TRUSTED_HOSTS` yourself — the chart refuses to render otherwise rather
than shipping a Deployment that cannot pass a probe.

## Migrations

`migrations.enabled` (default) runs `alembic upgrade head` in a `pre-install,
pre-upgrade` hook Job, and the api pods then boot with
`DB_MIGRATIONS_ON_STARTUP=false`. The Job reads hook-scoped copies of the
ConfigMap and the chart-managed Secret (`-migrate-config` / `-migrate-secrets`,
hook weight `-5`, deleted once it succeeds) and runs without the release
ServiceAccount: Helm applies ordinary resources only *after* its hooks, so
anything the Job referenced from the release itself would not exist on a first
install. An external `secrets.existingSecret` is referenced directly — it is
already there before Helm runs.

## Writable paths

`readOnlyRootFilesystem: true` leaves only the `/tmp` mount writable. Anything
that persists under the image tree — a plugin's own data directory, a Hugging
Face cache under `/app/models` — needs an entry in `extraVolumes` /
`extraVolumeMounts` (a PVC for durable state, an emptyDir for caches) or it
fails at boot.
