#!/usr/bin/env bash
#
# verify-backup.sh — validate a database backup so "we have backups" actually
# means "we have *restorable* backups".
#
# Two levels:
#   1. integrity  (default) — fast: gzip CRC check + non-empty + SQL sanity.
#   2. drill (--drill)      — thorough: restore into a throwaway Postgres
#                             container and run a sanity query, then tear down.
#
# Usage:
#   ./verify-backup.sh <backup_file.sql.gz> [--drill]
#
# Exit non-zero on any failure so it can gate a backup job / CI.

set -euo pipefail

BACKUP_FILE="${1:-}"
MODE="integrity"
[[ "${2:-}" == "--drill" ]] && MODE="drill"

if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
  echo "Usage: ./verify-backup.sh <backup_file.sql.gz> [--drill]" >&2
  exit 2
fi

echo "Verifying backup: $BACKUP_FILE (mode: $MODE)"

# --- Level 1: integrity ---------------------------------------------------
if [[ "$BACKUP_FILE" == *.gz ]]; then
  echo "  - gzip integrity (CRC)..."
  gzip -t "$BACKUP_FILE"
  SIZE=$(gunzip -c "$BACKUP_FILE" | wc -c | tr -d ' ')
  READER="gunzip -c"
else
  SIZE=$(wc -c < "$BACKUP_FILE" | tr -d ' ')
  READER="cat"
fi

if [[ "$SIZE" -lt 1 ]]; then
  echo "  ✗ Backup decompresses to 0 bytes." >&2
  exit 1
fi
echo "  - decompressed size: ${SIZE} bytes"

echo "  - SQL sanity (expecting a dump header)..."
if ! $READER "$BACKUP_FILE" | head -c 4096 | grep -qiE "PostgreSQL database dump|CREATE |INSERT |COPY |SET "; then
  echo "  ✗ No recognizable SQL/pg_dump content in the first 4KB." >&2
  exit 1
fi
echo "  ✓ Integrity checks passed."

if [[ "$MODE" == "integrity" ]]; then
  exit 0
fi

# --- Level 2: restore drill ----------------------------------------------
echo "  - Restore drill into a throwaway Postgres container..."
CONTAINER="baselith-backup-drill-$$"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER=drill -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=drill \
  "$PG_IMAGE" >/dev/null

echo "    waiting for postgres..."
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U drill >/dev/null 2>&1; then break; fi
  sleep 1
done

echo "    restoring..."
$READER "$BACKUP_FILE" | docker exec -i "$CONTAINER" psql -U drill -d drill >/dev/null

echo "    sanity query (count tables)..."
TABLES=$(docker exec "$CONTAINER" psql -U drill -d drill -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
echo "    public tables restored: ${TABLES}"
if [[ "${TABLES:-0}" -lt 1 ]]; then
  echo "  ✗ Restore drill produced no tables." >&2
  exit 1
fi

echo "  ✓ Restore drill succeeded (RTO measured = wall-clock of this run)."
