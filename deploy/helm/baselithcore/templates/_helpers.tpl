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
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}
