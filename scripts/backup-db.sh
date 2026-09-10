#!/bin/bash
# automated database backup script

# configure exit on failure
#
# `pipefail` is not decorative here: without it `set -e` only sees gzip's exit
# status, so a pg_dump that dies mid-pipe leaves the script printing "Backup
# created successfully" over a 20-byte gzip of nothing — and exiting 0, which
# is what any cron wrapper reports on.
set -euo pipefail

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/postgres"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "Starting database backup at $DATE"

# Dumped to a temporary name and renamed only once pg_dump has exited 0.
# Writing straight to the final name leaves a broken dump behind on failure —
# newer than every real backup, kept for the whole retention window, and picked
# first by anyone restoring "the latest". A failed backup must look like a
# missing one.
OUT="${BACKUP_DIR}/backup_${DATE}.sql.gz"
TMP="${BACKUP_DIR}/.backup_${DATE}.sql.gz.partial"
trap 'rm -f "${TMP}"' EXIT

# Create backup inside the postgres container and gzip it directly
# Assuming network is 'baselith-network' and DB name is 'baselithcore'
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U baselithcore baselithcore \
  | gzip > "${TMP}"
mv "${TMP}" "${OUT}"

echo "Backup created successfully at ${OUT}"

# Retain last 30 days of backups and delete older
find "${BACKUP_DIR}" -name "backup_*.sql.gz" -mtime +30 -delete
# Partials from runs killed before the trap could fire (reboot, SIGKILL).
find "${BACKUP_DIR}" -name ".backup_*.sql.gz.partial" -mmin +60 -delete
echo "Old backups cleaned. Finished."
