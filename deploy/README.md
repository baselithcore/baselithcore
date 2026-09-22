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

## Tracing

`telemetry.enabled` is what installs the OpenTelemetry providers and the
FastAPI / HTTPX / Redis auto-instrumentation. The SDK ships in the image, but
it does nothing until this is on — with it off there is no TracerProvider and
therefore **no spans at all**, which surfaces as an empty Traces tab in
BaselithControl on a cluster that is plainly serving requests.

```yaml
telemetry:
  enabled: true
  otlpEndpoint: ""      # no collector deployed — see below
  tracesSampleRate: 0.1 # production: sample, don't trace everything
  environment: production
```

**An empty `otlpEndpoint` is a supported mode, not an oversight.**
Instrumentation still runs and spans stay in-process, where the control-plane
trace viewer reads them; nothing is exported. The combination to avoid is an
endpoint pointing at a collector that is not deployed: the gRPC exporter then
retries every batch with backoff for the life of the pod, costing CPU and log
volume while still giving you no trace backend. The chart leaves the endpoint
empty by default for exactly this reason — the app's own default
(`http://localhost:4317`) assumes a sidecar collector that a pod does not have.

Two things to remember once a collector *does* exist:

- with `networkPolicy.egress.enabled`, a collector in another namespace needs
  an explicit `networkPolicy.egress.rules` entry — `presets.sameNamespace`
  does not cover it, and the export then fails silently at the CNI;
- `telemetry.metricsEnabled` requires the endpoint (the chart refuses the
  combination). OTel metrics have no in-process consumer here, and the
  Prometheus `/metrics` scrape that `serviceMonitor` uses is a separate,
  always-available pipeline.

`--set telemetry.tracesSampleRate=0.1` reaches the chart as the *string*
`"0.1"` — Helm's `--set` cannot produce floats — so the schema accepts both
forms. Either way it is validated to `0..1`.

Two exclusions are applied for you and are worth knowing about: the probes and
the `/metrics` scrape are not traced (they would otherwise dominate a
low-traffic deployment), and the ASGI `receive`/`send` sub-spans are dropped so
one request is one span instead of three. `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS`
and `BASELITH_OTEL_ASGI_SUB_SPANS=true` override them respectively.

## Alerts

`prometheusRule.enabled` renders a `PrometheusRule` with eleven rules: the RED
HTTP metrics the app already exports (`http_requests_total`,
`http_request_duration_seconds`), `mas_llm_*` and `security_events_total`, plus
kube-state-metrics and the kubelet's volume stats. It is off by default: a rule
object referencing a Prometheus that is not there is dead YAML, and a chart that
ships alerts nobody routed teaches operators to ignore the ones that do fire.

It does **not** replace `deploy/prometheus/*.yml`, and the two are not
alternatives to pick between. Those are file-based rules for the single-host
Prometheus the compose stack runs, including the SLO burn-rate alerts; this is
the Kubernetes half, and most of it could not exist there — nothing in a compose
deployment knows about a Deployment's replica count, a hook Job's exit status or
a PVC filling up. Where they do overlap, the error-rate rule deliberately reads
the same `http_requests_total` series that `slo-rules.yml` uses as its
availability SLI, so the alert and the SLO cannot drift apart.

Two properties are the whole reason these are worth having rather than a
starting point you rewrite.

**Every "nothing is running" rule is qualified by
`kube_deployment_spec_replicas > 0`.** A suspended deployment *is* zero
replicas with the data kept — that is how a customer cell is suspended — and
KEDA is allowed to scale the worker to zero on an empty queue. Both are desired
states, and at the kube-state-metrics level both are indistinguishable from an
outage. Without the guard every suspension pages somebody at 3am for something
that is off on purpose, which is how a team learns to close alerts without
reading them.

**The error rules are ratios, with a traffic guard.** An absolute threshold is
either useless on a deployment serving ten requests a minute or deaf on one
serving ten thousand. And a ratio over a zero denominator is `NaN`, which
compares false — so the denominator is required to be above zero explicitly,
or the rule would be silently off exactly when the deployment is quiet and
silently on when a single request fails.

The rest is knobs. `prometheusRule.thresholds` holds every number, because each
one is a judgement about a particular deployment's traffic and none of them
belongs in the template. `disabledAlerts` drops a rule by name, which keeps the
reason in git where an Alertmanager silence does not. `extraRules` is appended
to the group verbatim. Alerts for optional workloads render only when those
workloads do — no worker Deployment, no worker alert.

One failure mode is worth naming because it looks like the alerts are broken:
`prometheusRule.labels`. kube-prometheus-stack selects rule objects by label
(commonly `release: <stack release>`), so an object without the label is
created, accepted by the API server, and never evaluated. Check the Prometheus
resource's `ruleSelector` before looking anywhere else.

## Writable paths

`readOnlyRootFilesystem: true` leaves only the `/tmp` mount writable. Anything
that persists under the image tree — a plugin's own data directory, a Hugging
Face cache under `/app/models` — needs an entry in `extraVolumes` /
`extraVolumeMounts` (a PVC for durable state, an emptyDir for caches) or it
fails at boot.

One of those paths is a file the app *rewrites* rather than a directory it
merely fills, so a volume alone is not enough — the volume also has to start
with the image's copy. `seedFromImage` runs an initContainer that copies each `from`
(image path) to its `to` (a path under one of `extraVolumeMounts`) exactly
once, never overwriting a destination that already exists:

```yaml
config:
  # Must resolve inside the app's working directory (/app).
  PLUGIN_CONFIG_PATH: /app/data/configs/plugins.yaml
seedFromImage:
  - from: /app/configs/plugins.yaml
    to: /app/data/configs/plugins.yaml
```

`configs/plugins.yaml` is rewritten on every plugin enable/disable
(`baselith plugin config set`, or an admin surface that does the same); without
the move each toggle fails with `cannot write configs/plugins.yaml: [Errno 30]
Read-only file system`, and without the seed the plugins come up unconfigured.
A plugin that creates state directories per identity needs the same treatment
for its own root — a writable path, and a seed only if the image ships content
it cannot regenerate.

Moving that file off the image costs the deploy-time schema Job, and the chart
now says so instead of letting it through: `database.pluginSchemaInit` refuses
to render alongside a `PLUGIN_CONFIG_PATH` outside `/app/configs/`. The Job is
a hook pod and mounts none of these volumes, so the file is absent when it
runs — every plugin reads as enabled with an *empty* config block, and a
plugin that chooses its storage in that block builds the wrong one. The
symptom is a Job that prints `<plugin>: schema ready`, creates no table and
exits 0, followed by an application that boots against a schema which is not
there. A release that wants both the writable file and the Job has to give up
one of them: `plugins.config` below keeps the Job, and
`database.pluginSchemaInit.enabled: false` keeps the toggles — the second only
honestly when every plugin's tables come from a migration, or when the
deployment still lets the serving process hold DDL (`DB_RUNTIME_DDL=true`),
which a least-privilege role cannot do.

The opposite stance exists too. When the plugin set is a property of the
release rather than something an admin toggles at runtime — a per-customer
SaaS cell rendered from a spec, a GitOps overlay — put the `plugins.yaml`
content under `plugins.config` instead:

```yaml
plugins:
  config:
    auth: {enabled: true}
    api_routers: {enabled: true}
    baselithcontrol: {enabled: true, require_admin: false}
    compliance: {enabled: true, require_admin: true}
```

The chart renders it into a ConfigMap mounted read-only over
`/app/configs/plugins.yaml` in both pods (the path the app reads by default,
so no `PLUGIN_CONFIG_PATH`), rolls the pods when it changes, and refuses to
render alongside `config.PLUGIN_CONFIG_PATH` or a `seedFromImage` entry for
that file. Toggles from the console then fail with `Read-only file system`,
which is the point. List every plugin the release needs, system plugins
included: when the file has entries, a plugin absent from it is not loaded.

Point it at a **ReadWriteMany** volume when the api runs more than one
replica. `plugins.yaml` is per-filesystem: on ReadWriteOnce volumes a toggle
applied on one pod is invisible to the others until they restart onto the same
node.

## Backups

The `backup` CronJob dumps the database nightly (`pg_dump | gzip`, after
waiting for the database to answer — a CronJob replays missed schedules on a
cluster that just came back, ahead of CoreDNS). On its own it writes to
`backup.volume`, an emptyDir by default, which is a file on the node that just
died, not a backup. `backup.offsite` is what makes it one:

```yaml
backup:
  enabled: true
  serviceAccount:
    create: true
    annotations:
      eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/my-backups   # IRSA
  offsite:
    enabled: true
    remote: {type: s3, provider: AWS, env_auth: true, region: eu-west-1, no_check_bucket: true}
    path: my-backups-bucket/prod
    retentionDays: 90          # 0 = leave pruning to the bucket lifecycle
```

The dump becomes an init container (so it has finished, exit 0 and renamed
into place, before anything reads it) and an rclone container copies every
`backup_*.sql.gz` on the volume to the remote — `copy`, never `sync`, so the
remote keeps its own longer history. `remote` is any rclone backend: each key
becomes `RCLONE_CONFIG_OFFSITE_<KEY>`, so `type: gcs, env_auth: true` is a GCS
bucket through Workload Identity, `type: azureblob, env_auth: true, account:
<name>` Azure Blob through workload identity, and `provider: Other` plus
`endpoint` an S3-compatible store (OVH, MinIO). Where the provider has no pod
identity, `credentialsSecret` names a Secret carrying the same
`RCLONE_CONFIG_OFFSITE_*` variables with the static keys.

The backup runs as its own ServiceAccount (`backup.serviceAccount`) so the
cloud role that can write the bucket is granted to the backup alone, never to
the pods that serve requests; the Kubernetes token stays unmounted either way.
The chart refuses `offsite.enabled` without a `path` or a `remote.type`. Bound
the run with `activeDeadlineSeconds`: under `concurrencyPolicy: Forbid` a hung
upload blocks every later schedule.
