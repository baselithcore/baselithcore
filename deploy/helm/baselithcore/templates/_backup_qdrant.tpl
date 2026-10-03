{{/*
Whether the backup CronJob also snapshots the release's own Qdrant. The vectors
are derived data, but rebuilding them means re-embedding every document a
customer ever ingested: hours of inference on the shared TEI servers, and
nothing to rebuild from when the source documents were uploads rather than a
crawl. They go to the same volume and bucket as the database dump.
*/}}
{{- define "baselithcore.backupQdrantEnabled" -}}
{{- if and .Values.backup.enabled .Values.qdrant.enabled .Values.backup.qdrant.enabled -}}true{{- end -}}
{{- end -}}

{{/*
The Qdrant snapshot container. One snapshot per collection, through the API,
packed into a single `qdrant_<ts>.tar.gz` beside the database dump; restore a
collection by uploading its file to
`POST /collections/<name>/snapshots/upload?priority=snapshot`. Per-collection
snapshots rather than one full-storage snapshot because a full one restores
only by restarting Qdrant with `--storage-snapshot`, which replaces every
collection at once; the per-collection ones restore through the API, one
collection at a time, into a running server.
*/}}
{{- define "baselithcore.backupQdrantContainer" -}}
- name: qdrant-snapshot
  image: {{ .Values.backup.qdrant.image | quote }}
  securityContext:
    {{- toYaml .Values.securityContext | nindent 4 }}
  command:
    - /bin/sh
    - -c
    - |
      set -euo pipefail
      TS=$(date +%Y%m%d_%H%M%S)
      OUT="${BACKUP_DIR}/qdrant_${TS}.tar.gz"
      # Same contract as the dump: work under a hidden partial name and
      # rename only once every collection is in, so a failed run leaves
      # nothing that looks like a backup.
      WORK="${BACKUP_DIR}/.qdrant_${TS}.partial"
      TMP="${BACKUP_DIR}/.qdrant_${TS}.tar.gz.partial"
      trap 'rm -rf "${WORK}" "${TMP}"' EXIT
      mkdir -p "${WORK}/collections"
      # The key goes in a header file, not on curl's command line.
      (umask 077; : > "${WORK}/headers")
      if [ -n "${QDRANT_API_KEY:-}" ]; then
        (umask 077; printf 'api-key: %s\n' "${QDRANT_API_KEY}" > "${WORK}/headers")
      fi
      q() { curl -fsS --retry 3 --retry-delay 5 --retry-all-errors -H "@${WORK}/headers" "$@"; }
      # A CronJob replaying missed schedules starts during a cluster boot,
      # often before Qdrant has loaded its collections.
      DEADLINE=$(( $(date +%s) + WAIT_SECONDS ))
      until curl -fsS -o /dev/null "${QDRANT_URL}/readyz"; do
        if [ "$(date +%s)" -ge "${DEADLINE}" ]; then
          echo "qdrant ${QDRANT_URL} not ready after ${WAIT_SECONDS}s, giving up" >&2
          exit 1
        fi
        echo "waiting for ${QDRANT_URL} ..."
        sleep 5
      done
      # Listed to a file first: a refused key (401) must fail the run, not
      # read as "no collections" and pass as an empty backup.
      q -o "${WORK}/collections.json" "${QDRANT_URL}/collections"
      { grep -o '"name":"[^"]*"' "${WORK}/collections.json" || true; } \
        | cut -d'"' -f4 > "${WORK}/collections.txt"
      echo "Snapshotting $(wc -l < "${WORK}/collections.txt") collection(s) from ${QDRANT_URL}"
      while read -r COLLECTION; do
        SNAP=$(q -X POST "${QDRANT_URL}/collections/${COLLECTION}/snapshots?wait=true" \
          | grep -o '"name":"[^"]*"' | head -n 1 | cut -d'"' -f4)
        if [ -z "${SNAP}" ]; then
          echo "no snapshot name returned for ${COLLECTION}" >&2
          exit 1
        fi
        q -o "${WORK}/collections/${COLLECTION}.snapshot" \
          "${QDRANT_URL}/collections/${COLLECTION}/snapshots/${SNAP}"
        # Each snapshot is a full copy of the collection on Qdrant's own
        # disk: left there, a month of nightly runs fills it.
        q -X DELETE "${QDRANT_URL}/collections/${COLLECTION}/snapshots/${SNAP}" > /dev/null \
          || echo "could not delete ${SNAP} on the server" >&2
        echo "  ${COLLECTION}: ${SNAP}"
      done < "${WORK}/collections.txt"
      tar -czf "${TMP}" -C "${WORK}" collections.txt collections
      mv "${TMP}" "${OUT}"
      echo "Pruning Qdrant snapshots older than ${RETENTION_DAYS} days"
      find "${BACKUP_DIR}" -name 'qdrant_*.tar.gz' -mtime +${RETENTION_DAYS} -delete
      find "${BACKUP_DIR}" -name '.qdrant_*.partial' -mmin +60 -exec rm -rf {} +
      find "${BACKUP_DIR}" -name '.qdrant_*.tar.gz.partial' -mmin +60 -delete
      echo "Qdrant snapshot complete: ${OUT}"
  {{- with .Values.backup.qdrant.resources }}
  resources:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  env:
    - name: BACKUP_DIR
      value: /backups
    - name: RETENTION_DAYS
      value: {{ .Values.backup.retentionDays | quote }}
    - name: WAIT_SECONDS
      value: {{ .Values.backup.waitForDatabaseSeconds | quote }}
    - name: QDRANT_URL
      value: {{ printf "http://%s:6333" (include "baselithcore.qdrantName" .) | quote }}
    - name: QDRANT_API_KEY
      valueFrom:
        secretKeyRef:
          name: {{ include "baselithcore.secretName" . }}
          key: BASELITH_QDRANT_API_KEY
          # Same as the server: a cell from before the key existed runs open.
          optional: true
  volumeMounts:
    - name: backups
      mountPath: /backups
{{- end -}}
