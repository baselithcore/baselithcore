#!/usr/bin/env bash

# restore-db.sh
# Restore a PostgreSQL backup: plain SQL, or the gzipped dump (.sql.gz) that
# backup-db.sh and the Helm backup CronJob write.
#
# Usage:
#   ./restore-db.sh <backup_file>            # into the compose.prod.yaml postgres service
#   ./restore-db.sh <backup_file> --direct   # into the database the libpq environment
#                                            # names (PGHOST, PGPORT, PGUSER, PGPASSWORD,
#                                            # PGDATABASE): a managed instance, a
#                                            # `kubectl port-forward`, the nightly drill
#
# The target database must be empty. The restore stops at the first SQL error
# and runs as one transaction, so a failed restore leaves the target untouched
# and exits non-zero — psql's default is to report the error, carry on with the
# next statement and exit 0, which turned a half-applied restore into "Database
# restoration completed successfully".

set -euo pipefail

BACKUP_FILE="${1:-}"
MODE="compose"
[[ "${2:-}" == "--direct" ]] && MODE="direct"

if [ -z "$BACKUP_FILE" ]; then
  echo "Usage: ./restore-db.sh <path_to_backup_file.sql[.gz]> [--direct]"
  exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Error: Backup file '$BACKUP_FILE' does not exist."
  exit 1
fi

# Every restore below goes through these flags. `-f -` rather than a bare
# stdin: --single-transaction is documented for -f/-c input.
PSQL_RESTORE_FLAGS=(-v ON_ERROR_STOP=1 --single-transaction --quiet)

echo "Starting database restoration from $BACKUP_FILE (mode: $MODE)..."

if [[ "$MODE" == "direct" ]]; then
  echo "Waiting for ${PGHOST:-localhost}:${PGPORT:-5432} to be ready..."
  for _ in $(seq 1 30); do
    if pg_isready -q; then break; fi
    sleep 2
  done
  pg_isready
  if [[ "$BACKUP_FILE" == *.gz ]]; then
    echo "Detected gzipped backup; decompressing in-stream..."
    gunzip -c "$BACKUP_FILE" | psql "${PSQL_RESTORE_FLAGS[@]}" -f -
  else
    psql "${PSQL_RESTORE_FLAGS[@]}" -f "$BACKUP_FILE"
  fi
  echo "Database restoration completed successfully."
  exit 0
fi

# Wait for the database container to be ready
echo "Waiting for postgres to be ready..."
until docker compose -f compose.prod.yaml exec postgres pg_isready -U baselithcore; do
  sleep 2
done

# backup-db.sh produces gzipped dumps (.sql.gz); decompress transparently.
if [[ "$BACKUP_FILE" == *.gz ]]; then
  echo "Detected gzipped backup; decompressing in-stream..."
  gunzip -c "$BACKUP_FILE" \
    | docker compose -f compose.prod.yaml exec -T postgres \
        psql "${PSQL_RESTORE_FLAGS[@]}" -U baselithcore -d baselithcore -f -
else
  # Plain SQL: copy into the container and apply.
  echo "Copying backup file to container..."
  docker cp "$BACKUP_FILE" "$(docker compose -f compose.prod.yaml ps -q postgres)":/tmp/backup.sql
  echo "Restoring database..."
  docker compose -f compose.prod.yaml exec postgres \
    psql "${PSQL_RESTORE_FLAGS[@]}" -U baselithcore -d baselithcore -f /tmp/backup.sql
  echo "Cleaning up..."
  docker compose -f compose.prod.yaml exec postgres rm /tmp/backup.sql
fi

echo "Database restoration completed successfully."
