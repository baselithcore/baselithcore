# Staging Provisioning (B1)

How to stand up the persistent staging instance — the **relevant environment**
defined in [V&V Plan §3](vv-plan.md#3-validation-environments) — from the IaC
already in the repository. Evidence row
[B1 of the TRL 5 matrix](trl5-evidence-matrix.md) converts to ✅ when this
environment exists and campaign reports reference it.

## Prerequisites (cluster side)

- A Kubernetes cluster (any conformant distribution; a 3-node cluster with
  4 vCPU / 8 GiB nodes is ample for the staging profile) and a `kubectl`
  context pointing at it.
- **nginx ingress controller** and **cert-manager** with a `letsencrypt-prod`
  ClusterIssuer (or edit the annotations in `values-staging.yaml` to match
  your issuer/ingress class).
- **Prometheus Operator** (kube-prometheus-stack) — `serviceMonitor.enabled`
  is on in the staging overlay; campaign verdicts and the soak memory-trend
  check (V3) read from Prometheus.
- A DNS record for the staging hostname pointing at the ingress.
- PostgreSQL, Redis, and Qdrant reachable from the cluster (in-cluster
  deployments or managed services) — the chart consumes their endpoints via
  config/secret values.

## Option A — Terraform (recommended)

The module in `deploy/terraform/` creates the namespace, a Terraform-managed
Secret (credentials never enter Helm values or the release manifest), and the
Helm release:

```bash
cd deploy/terraform
cp terraform.tfvars.staging.example terraform.tfvars   # gitignored — fill in
terraform init
terraform apply
```

Fill in: staging hostname (`set_values`), real `app_secrets` (including
`ANTHROPIC_API_KEY` — campaigns require a real provider), `image_tag`.
The release name `baselithcore-staging` yields the Secret
`baselithcore-staging-secrets`, which is exactly the `existingSecret`
referenced by `values-staging.yaml`.

## Option B — plain Helm

```bash
kubectl create namespace baselithcore-staging
kubectl -n baselithcore-staging create secret generic baselithcore-staging-secrets \
  --from-literal=SECRET_KEY=... \
  --from-literal=DATA_ENCRYPTION_KEYS=... \
  --from-literal=DB_PASSWORD=... \
  --from-literal=ANTHROPIC_API_KEY=...
helm upgrade --install baselithcore-staging deploy/helm/baselithcore \
  -n baselithcore-staging \
  -f deploy/helm/baselithcore/values-staging.yaml \
  --set ingress.hosts[0].host=staging.your-domain.tld \
  --set ingress.tls[0].hosts[0]=staging.your-domain.tld
```

## Load the SLO rules

Campaign verdicts are judged against `deploy/prometheus/slo-rules.yml`. With
Prometheus Operator, wrap the two rule groups in a `PrometheusRule` object in
the staging namespace; with plain Prometheus, add the file under `rule_files`.
Keep `deploy/prometheus/alert-rules.yml` alongside for burn-rate alerting.

## Post-provision smoke

```bash
curl -fsS https://staging.your-domain.tld/health          # liveness
API_BASE_URL=https://staging.your-domain.tld tests/smoke_chat.sh  # chat path (real provider)
python tests/load/campaign.py --profile smoke \
  --host https://staging.your-domain.tld \
  --out validation-reports/$(date +%F)-staging-smoke
```

A green smoke campaign is the B1 acceptance evidence — commit its report.
Then proceed with the [validation campaigns](campaigns.md) in order:
load baseline → soak → chaos → agentic eval.

## Teardown / rebuild

The environment must be **reproducible**: `terraform destroy && terraform
apply` (or `helm uninstall` + reinstall) should yield an equivalent
environment. If it doesn't, whatever was configured by hand is missing from
this page or the IaC — fix that first; hand-configured staging invalidates
the "reproducible relevant environment" claim.
