# Kubernetes Deployment (Helm)

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
| Spread | `topologySpreadConstraints` across nodes |
| Config / secrets | `ConfigMap` (non-secret) + `Secret` (chart-managed or external) via `envFrom` |
| Metrics | optional `ServiceMonitor` scraping `/metrics` |
| Network | optional `NetworkPolicy` |
| Workers | optional `core.task_queue` worker `Deployment` |

## Quick start

```bash
helm upgrade --install baselithcore deploy/helm/baselithcore \
  -n baselithcore --create-namespace \
  -f deploy/helm/baselithcore/values-production.yaml \
  --set-string secrets.existingSecret=baselithcore-secrets
```

`values-production.yaml` is a ready-to-edit overlay (ingress, TLS via
cert-manager, HPA 3–20, workers, ServiceMonitor, NetworkPolicy).

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
