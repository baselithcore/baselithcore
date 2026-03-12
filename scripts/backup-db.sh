#!/bin/bash
# automated database backup script

# configure exit on failure
set -e

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/postgres"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "Starting database backup at $DATE"

# Create backup inside the postgres container and gzip it directly
# Assuming network is 'baselith-network' and DB name is 'baselithcore'
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U baselithcore baselithcore \
  | gzip > "${BACKUP_DIR}/backup_${DATE}.sql.gz"

echo "Backup created successfully at ${BACKUP_DIR}/backup_${DATE}.sql.gz"

# Retain last 30 days of backups and delete older
find "${BACKUP_DIR}" -name "backup_*.sql.gz" -mtime +30 -delete
echo "Old backups cleaned. Finished."
