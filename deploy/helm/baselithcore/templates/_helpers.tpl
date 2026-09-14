{{/*
Common template helpers.
*/}}

{{- define "baselithcore.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "baselithcore.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "baselithcore.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "baselithcore.labels" -}}
helm.sh/chart: {{ include "baselithcore.chart" . }}
{{ include "baselithcore.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: baselithcore
{{- end -}}

{{- define "baselithcore.selectorLabels" -}}
app.kubernetes.io/name: {{ include "baselithcore.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "baselithcore.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "baselithcore.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret holding sensitive env (chart-managed or external). */}}
{{- define "baselithcore.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "baselithcore.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* Name of the ConfigMap carrying the declarative plugin set (`plugins.config`). */}}
{{- define "baselithcore.pluginsConfigName" -}}
{{- printf "%s-plugins" (include "baselithcore.fullname" .) -}}
{{- end -}}

{{/*
Volume + mount for the declarative plugin set. Rendered into both pod specs
only when `plugins.config` is non-empty, so a chart without it renders
byte-identically to before the key existed. `subPath` mounts a single file
over the image's configs/plugins.yaml without hiding the rest of configs/;
the pods roll on content changes through the checksum/plugins annotation,
which is also why a subPath (which does not live-update) is fine here.
*/}}
{{- define "baselithcore.pluginsConfigVolumeMount" -}}
{{- if .Values.plugins.config }}
- name: plugins-config
  mountPath: /app/configs/plugins.yaml
  subPath: plugins.yaml
  readOnly: true
{{- end }}
{{- end -}}

{{- define "baselithcore.pluginsConfigVolume" -}}
{{- if .Values.plugins.config }}
- name: plugins-config
  configMap:
    name: {{ include "baselithcore.pluginsConfigName" . }}
{{- end }}
{{- end -}}

{{/*
Names of the env sources the pre-deploy migration hook reads.

The hook Job runs BEFORE Helm applies the release's ordinary resources, so it
cannot use the ConfigMap and the chart-managed Secret the app pods use — on a
first install neither exists yet and the Job's pod is unschedulable until
activeDeadlineSeconds kills it (and the install). It therefore gets its own
hook-scoped copies, created at a lower hook weight and deleted once the Job
succeeds. An externally-managed `secrets.existingSecret` needs no copy: it is
already there before Helm runs.
*/}}
{{- define "baselithcore.migrationConfigName" -}}
{{- printf "%s-migrate-config" (include "baselithcore.fullname" .) -}}
{{- end -}}

{{- define "baselithcore.migrationSecretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-migrate-secrets" (include "baselithcore.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
In-cluster DNS names of the API Service. Always part of the derived
TRUSTED_HOSTS: the probes, and any in-cluster caller, address the pod through
these rather than through the public ingress host.
*/}}
{{- define "baselithcore.serviceHosts" -}}
{{- $fullname := include "baselithcore.fullname" . -}}
{{- $ns := .Release.Namespace -}}
{{- list $fullname (printf "%s.%s" $fullname $ns) (printf "%s.%s.svc" $fullname $ns) (printf "%s.%s.svc.cluster.local" $fullname $ns) | toJson -}}
{{- end -}}

{{/*
Host header the kubelet must send on the probes.

`TRUSTED_HOSTS` mounts Starlette's TrustedHostMiddleware, which rejects any
request whose Host it does not know. The kubelet addresses the pod by IP, so
without this header every probe gets a 400 and the pod never becomes ready —
for the full startupProbe window, with nothing in the logs but the 400s.

Explicit `probeHost` wins. Otherwise the Service FQDN is used, which the chart
guarantees is in the list it derives. When the operator sets
`config.TRUSTED_HOSTS` by hand the chart cannot know which of their names the
probes may claim to be, so it refuses to render rather than ship a Deployment
that can never pass a probe.
*/}}
{{- define "baselithcore.probeHost" -}}
{{- if .Values.probeHost -}}
{{- .Values.probeHost -}}
{{- else if hasKey .Values.config "TRUSTED_HOSTS" -}}
{{- fail "config.TRUSTED_HOSTS is set by hand, so probeHost must be set too: TrustedHostMiddleware answers 400 to the kubelet's Host (the pod IP) and the pod never becomes ready. Set probeHost to one of the names in your TRUSTED_HOSTS." -}}
{{- else -}}
{{- printf "%s.%s.svc.cluster.local" (include "baselithcore.fullname" .) .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/*
Hosts accepted by TrustedHostMiddleware, as the JSON list the app parses.

`APP_ENV=production` with an empty TRUSTED_HOSTS is a hard startup abort
(core/api/startup_checks.py), and the chart ships APP_ENV=production — so a
default install crash-loops unless the deployment's own names are carried
through. Ingress hosts (when the ingress is on) plus the in-cluster Service
names, which is what the probes and in-cluster callers use.
*/}}
{{- define "baselithcore.trustedHosts" -}}
{{- $hosts := list -}}
{{- if .Values.ingress.enabled -}}
{{- range .Values.ingress.hosts -}}
{{- if .host -}}
{{- $hosts = append $hosts .host -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $hosts = concat $hosts (include "baselithcore.serviceHosts" . | fromJsonArray) -}}
{{- $hosts | uniq | toJson -}}
{{- end -}}

{{/*
The same list with the pod's own IP appended, for the container env.

A ServiceMonitor endpoint has no Host-header field: Prometheus derives the Host
from `__address__`, which the operator fills with the pod IP. So the scrape
arrives as `Host: 10.x.y.z` — a name TrustedHostMiddleware does not know — and
answers 400, leaving the target permanently down. Rewriting `__address__` to
the Service instead would collapse every replica onto one address, so the pod
IP has to become a trusted name.

`$(POD_IP)` is expanded by the kubelet from the downward-API entry declared
just before it in the container's env, which is why this cannot live in the
ConfigMap: envFrom values are never expanded.
*/}}
{{- define "baselithcore.trustedHostsWithPodIP" -}}
{{- if hasKey .Values.config "TRUSTED_HOSTS" -}}
{{- fail "config.TRUSTED_HOSTS is set by hand while serviceMonitor.trustPodIP is on: the chart will not rewrite a list it does not own. Either drop config.TRUSTED_HOSTS and let the chart derive it, or set serviceMonitor.trustPodIP=false and add the pod IP yourself (an extraEnv TRUSTED_HOSTS whose value ends with $(POD_IP), plus a POD_IP fieldRef declared before it)." -}}
{{- end -}}
{{- $hosts := include "baselithcore.trustedHosts" . | fromJsonArray -}}
{{- append $hosts "$(POD_IP)" | toJson -}}
{{- end -}}

{{/*
Browser origins allowed to POST, as the JSON list the app parses.

``ALLOW_ORIGINS`` defaults to empty, and CSRFOriginMiddleware rejects every
request that carries an ``Origin`` the list does not contain. A browser sends
one on each cross-origin-capable request, so an empty list means the SPA loads
and then every login fails with "CSRF check failed: origin not allowed" — while
curl, which sends no Origin, succeeds and hides the problem. The scheme follows
whether the host is covered by an ``ingress.tls`` entry.
*/}}
{{- define "baselithcore.allowOrigins" -}}
{{- $tlsHosts := list -}}
{{- range .Values.ingress.tls -}}
{{- range .hosts -}}
{{- $tlsHosts = append $tlsHosts . -}}
{{- end -}}
{{- end -}}
{{- $origins := list -}}
{{- range .Values.ingress.hosts -}}
{{- if .host -}}
{{- $scheme := ternary "https" "http" (has .host $tlsHosts) -}}
{{- $origins = append $origins (printf "%s://%s" $scheme .host) -}}
{{- end -}}
{{- end -}}
{{- $origins | uniq | toJson -}}
{{- end -}}

{{/*
Non-secret env, shared by the ConfigMap the pods read and the hook-scoped copy
the migration Job reads, so the two can never drift.
*/}}
{{- define "baselithcore.configData" -}}
{{- range $key, $value := .Values.config }}
{{ $key }}: {{ $value | quote }}
{{- end }}
{{- if not (hasKey .Values.config "TRUSTED_HOSTS") }}
TRUSTED_HOSTS: {{ include "baselithcore.trustedHosts" . | quote }}
{{- end }}
{{- if and (not (hasKey .Values.config "ALLOW_ORIGINS")) .Values.ingress.enabled }}
ALLOW_ORIGINS: {{ include "baselithcore.allowOrigins" . | quote }}
{{- end }}
{{- include "baselithcore.telemetryConfigData" . }}
{{- end -}}

{{/*
Telemetry env for the ConfigMap (api and worker both consume it via envFrom).

Every key is skipped when `.Values.config` already carries it: the two would
otherwise render as duplicate keys in one ConfigMap `data` map, where the
winner depends on YAML merge order rather than on anything the operator chose.
*/}}
{{- define "baselithcore.telemetryConfigData" -}}
{{- if .Values.telemetry.enabled }}
{{- if and .Values.telemetry.metricsEnabled (not .Values.telemetry.otlpEndpoint) }}
{{- fail "telemetry.metricsEnabled requires telemetry.otlpEndpoint: OTel metrics have no in-process consumer, so without a collector the periodic export thread exports nowhere. Use the Prometheus /metrics scrape (serviceMonitor) instead." }}
{{- end }}
{{- if not (hasKey .Values.config "TELEMETRY_ENABLED") }}
TELEMETRY_ENABLED: "true"
{{- end }}
{{- if not (hasKey .Values.config "TELEMETRY_OTEL_ENDPOINT") }}
{{- /* Always emitted, empty included: the app default is localhost:4317, and
inheriting it in a pod with no sidecar collector is the retry-forever case. */}}
TELEMETRY_OTEL_ENDPOINT: {{ .Values.telemetry.otlpEndpoint | quote }}
{{- end }}
{{- if not (hasKey .Values.config "TELEMETRY_TRACES_SAMPLE_RATE") }}
TELEMETRY_TRACES_SAMPLE_RATE: {{ .Values.telemetry.tracesSampleRate | quote }}
{{- end }}
{{- if not (hasKey .Values.config "TELEMETRY_METRICS_ENABLED") }}
TELEMETRY_METRICS_ENABLED: {{ .Values.telemetry.metricsEnabled | quote }}
{{- end }}
{{- if and .Values.telemetry.environment (not (hasKey .Values.config "DEPLOYMENT_ENVIRONMENT")) }}
DEPLOYMENT_ENVIRONMENT: {{ .Values.telemetry.environment | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Probe httpGet block, shared by the three probes. */}}
{{- define "baselithcore.probeHttpGet" -}}
path: {{ .path }}
port: http
{{- with .host }}
httpHeaders:
  - name: Host
    value: {{ . | quote }}
{{- end }}
{{- end -}}

{{/* Image reference, defaulting the tag to the chart appVersion. */}}
{{- define "baselithcore.image" -}}
{{- if .Values.image.digest -}}
{{- /* A digest is the whole reference: a tag alongside it is ignored by the
runtime and only misleads whoever reads the manifest. */ -}}
{{- if not (hasPrefix "sha256:" .Values.image.digest) -}}
{{- fail (printf "image.digest must be a full digest starting with 'sha256:'; got %q" .Values.image.digest) -}}
{{- end -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}
{{- end -}}

{{/*
initContainer that seeds mounted volumes from the image (`seedFromImage`).

Every path in the image tree is read-only under `readOnlyRootFilesystem`, so a
file the app rewrites at runtime lives on a volume instead — and a fresh volume
starts empty, without the image's default. This copies the default across once,
before the app container starts, and never overwrites a destination that
already exists: what the running deployment wrote survives restarts, upgrades
and a re-pulled image.

The copy lands on a per-pod temporary name and is moved into place with a
single rename, so a worker pod seeding the same volume concurrently can never
read a half-written file.
*/}}
{{- define "baselithcore.seedInitContainers" -}}
{{- if .Values.seedFromImage }}
initContainers:
  - name: seed-from-image
    image: {{ include "baselithcore.image" . }}
    imagePullPolicy: {{ .Values.image.pullPolicy }}
    securityContext:
      {{- toYaml .Values.securityContext | nindent 6 }}
    command:
      - sh
      - -c
      - |
        set -eu
        {{- range .Values.seedFromImage }}
        {{- if not (and .from .to) }}
        {{- fail "each seedFromImage entry needs both `from` (a path in the image) and `to` (a path under one of extraVolumeMounts)" }}
        {{- end }}
        {{- if not (hasPrefix "/" .to) }}
        {{- fail (printf "seedFromImage `to` must be absolute; got %s" .to) }}
        {{- end }}
        if [ -e {{ .to | squote }} ]; then
          echo "seed: {{ .to }} already present, leaving it alone"
        else
          mkdir -p "$(dirname {{ .to | squote }})"
          tmp={{ .to | squote }}.seeding.$$
          rm -rf "$tmp"
          cp -a {{ .from | squote }} "$tmp"
          mv -T "$tmp" {{ .to | squote }} || rm -rf "$tmp"
          echo "seed: {{ .from }} -> {{ .to }}"
        fi
        {{- end }}
    volumeMounts:
      {{- if .Values.tmpVolumes.enabled }}
      - name: tmp
        mountPath: /tmp
      {{- end }}
      {{- with .Values.extraVolumeMounts }}
      {{- toYaml . | nindent 6 }}
      {{- end }}
{{- end }}
{{- end -}}

{{/*
topologySpreadConstraints for one component.

Hostname keeps a single node from taking the whole deployment; zone keeps a
single availability zone from doing the same, which is the constraint most
charts leave out and the one that matters during a real cloud incident. On a
single-zone cluster the zone constraint is trivially satisfied — every node
reports the same value — so it costs nothing to ship on by default.

`matchLabelKeys: [pod-template-hash]` scopes the skew calculation to the
ReplicaSet being rolled: without it, pods from the outgoing ReplicaSet count
towards the new one's spread and a rollout can wedge itself against its own
predecessor.

Args: root (the chart context), component ("api" / "worker").
*/}}
{{- define "baselithcore.topologySpread" -}}
{{- $root := .root -}}
{{- $component := .component -}}
{{- $when := $root.Values.topologySpreadConstraints.whenUnsatisfiable | default "ScheduleAnyway" -}}
{{- $keys := list "kubernetes.io/hostname" -}}
{{- if $root.Values.topologySpreadConstraints.zoneAware -}}
{{- $keys = append $keys "topology.kubernetes.io/zone" -}}
{{- end -}}
{{- range $keys }}
- maxSkew: 1
  topologyKey: {{ . }}
  whenUnsatisfiable: {{ $when }}
  matchLabelKeys:
    - pod-template-hash
  labelSelector:
    matchLabels:
      {{- include "baselithcore.selectorLabels" $root | nindent 6 }}
      app.kubernetes.io/component: {{ $component }}
{{- end }}
{{- end -}}

{{/*
Environment shared by the backup CronJob's wait-for-database init container and
its dump container. Defined once because the two have to agree: an init
container that probes a different host than the one pg_dump later contacts is
worse than no probe at all — it reports the database ready and the dump then
fails against something else.
*/}}
{{- define "baselithcore.backupEnv" -}}
- name: BACKUP_DIR
  value: /backups
- name: RETENTION_DAYS
  value: {{ .Values.backup.retentionDays | quote }}
- name: PGHOST
  value: {{ .Values.backup.pg.host | quote }}
- name: PGPORT
  value: {{ .Values.backup.pg.port | quote }}
- name: PGDATABASE
  value: {{ .Values.backup.pg.database | quote }}
- name: PGUSER
  value: {{ .Values.backup.pg.user | quote }}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.backup.pg.passwordSecret.name | default (include "baselithcore.secretName" .) }}
      key: {{ .Values.backup.pg.passwordSecret.key }}
{{- end -}}

{{/*
ServiceAccount the backup pod runs as. Empty means the namespace default: the
CronJob then carries no serviceAccountName at all, exactly as before the
`backup.serviceAccount` block existed.
*/}}
{{- define "baselithcore.backupServiceAccountName" -}}
{{- if .Values.backup.serviceAccount.create -}}
{{- default (printf "%s-backup" (include "baselithcore.fullname" .)) .Values.backup.serviceAccount.name -}}
{{- else -}}
{{- .Values.backup.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
The pg_dump container. It is the pod's main container when the dump stays on
the volume, and an init container (so it has finished before the upload
starts) when `backup.offsite` is on — same container either way.
*/}}
{{- define "baselithcore.backupDumpContainer" -}}
- name: pg-backup
  image: {{ .Values.backup.image | quote }}
  securityContext:
    {{- toYaml .Values.securityContext | nindent 4 }}
  command:
    - /bin/sh
    - -c
    - |
      set -euo pipefail
      TS=$(date +%Y%m%d_%H%M%S)
      OUT="${BACKUP_DIR}/backup_${TS}.sql.gz"
      # Dumped to a temporary name and renamed only once pg_dump
      # has exited 0. Writing straight to $OUT leaves the file
      # behind when the dump fails — a 20-byte gzip of nothing,
      # newer than every real backup, kept for the whole retention
      # window, and picked first by anyone restoring "the latest".
      # A failed backup must look like a missing one.
      TMP="${BACKUP_DIR}/.backup_${TS}.sql.gz.partial"
      trap 'rm -f "${TMP}"' EXIT
      echo "Dumping ${PGDATABASE} -> ${OUT}"
      pg_dump | gzip > "${TMP}"
      mv "${TMP}" "${OUT}"
      echo "Pruning backups older than ${RETENTION_DAYS} days"
      find "${BACKUP_DIR}" -name 'backup_*.sql.gz' -mtime +${RETENTION_DAYS} -delete
      # Interrupted runs (OOM, node eviction, the deadline above)
      # never reach the trap, so their partials are swept here.
      find "${BACKUP_DIR}" -name '.backup_*.sql.gz.partial' -mmin +60 -delete
      echo "Backup complete."
  env:
    {{- include "baselithcore.backupEnv" . | nindent 4 }}
  volumeMounts:
    - name: backups
      mountPath: /backups
{{- end -}}

{{/*
The rclone remote named `offsite`, defined through the environment: every key
of backup.offsite.remote becomes RCLONE_CONFIG_OFFSITE_<KEY>. Booleans and
numbers are stringified, which is what rclone expects from the environment.
*/}}
{{- define "baselithcore.backupOffsiteRemoteEnv" -}}
{{- range $key, $value := .Values.backup.offsite.remote }}
- name: RCLONE_CONFIG_OFFSITE_{{ $key | upper }}
  value: {{ $value | toString | quote }}
{{- end }}
{{- end -}}
