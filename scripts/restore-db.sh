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

# Copy the backup file into the container
echo "Copying backup file to container..."
docker cp "$BACKUP_FILE" "$(docker compose -f docker-compose.prod.yml ps -q postgres)":/tmp/backup.sql

# Execute the restoration
echo "Restoring database..."
docker compose -f docker-compose.prod.yml exec postgres psql -U baselithcore -d baselithcore -f /tmp/backup.sql

# Clean up
echo "Cleaning up..."
docker compose -f docker-compose.prod.yml exec postgres rm /tmp/backup.sql

echo "Database restoration completed successfully."
