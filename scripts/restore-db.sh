#!/usr/bin/env bash

# restore-db.sh
# Script to restore the PostgreSQL database from a given backup file.
# Usage: ./restore-db.sh <path_to_backup_file>

set -e

if [ -z "$1" ]; then
  echo "Usage: ./restore-db.sh <path_to_backup_file.sql>"
  exit 1
fi

BACKUP_FILE=$1

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Error: Backup file '$BACKUP_FILE' does not exist."
  exit 1
fi

echo "Starting database restoration from $BACKUP_FILE..."

# Wait for the database container to be ready
echo "Waiting for postgres to be ready..."
until docker compose -f docker-compose.prod.yml exec postgres pg_isready -U baselithcore; do
  sleep 2
done

# backup-db.sh produces gzipped dumps (.sql.gz); decompress transparently.
if [[ "$BACKUP_FILE" == *.gz ]]; then
  echo "Detected gzipped backup; decompressing in-stream..."
  gunzip -c "$BACKUP_FILE" \
    | docker compose -f docker-compose.prod.yml exec -T postgres \
        psql -U baselithcore -d baselithcore
else
  # Plain SQL: copy into the container and apply.
  echo "Copying backup file to container..."
  docker cp "$BACKUP_FILE" "$(docker compose -f docker-compose.prod.yml ps -q postgres)":/tmp/backup.sql
  echo "Restoring database..."
  docker compose -f docker-compose.prod.yml exec postgres psql -U baselithcore -d baselithcore -f /tmp/backup.sql
  echo "Cleaning up..."
  docker compose -f docker-compose.prod.yml exec postgres rm /tmp/backup.sql
fi

echo "Database restoration completed successfully."
