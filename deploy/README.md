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
