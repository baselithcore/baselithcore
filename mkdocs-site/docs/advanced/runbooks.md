# Operations Runbooks

This guide outlines emergency procedures and operational runbooks for maintaining a production deployment of BaselithCore.

## Emergency Procedures

### Database Restoration

If the primary database is corrupted or data is lost, you can restore from a backup using the provided shell script.

**Prerequisites:**

- Ensure you have a valid SQL backup file covering your desired point-in-time state.
- Ensure Docker Compose is running.

**Steps:**

1. Locate your backup file (e.g., `backup_2023-11-01.sql`).
2. Run the restoration script from the project root:

   ```bash
   ./scripts/restore-db.sh /path/to/backup_2023-11-01.sql
   ```

3. The script handles both plain `.sql` and gzipped `.sql.gz` backups
   (it decompresses `.gz` in-stream).

### Handling High Error Rate Alerts

When Prometheus triggers a **HighErrorRate** alert (HTTP 5xx > 5%):

1. Check the logs for the `api` and `worker` services:

   ```bash
   docker compose -f docker-compose.prod.yml logs --tail 200 api worker
   ```

2. Identify the source of the 5xx errors. Common causes include:
   - External provider failures (e.g., OpenAI API down).
   - Database connection limits exhausted.
3. If an external provider is down, the Circuit Breaker should open automatically and fail fast. Monitor the logs for "Circuit breaker OPEN" messages.
4. Scale up the `api` or `worker` services if the load is overwhelming the components.

### Handling API Latency Alerts

When Prometheus triggers a **HighApiLatency** alert (P95 > 2s):

1. Investigate the Jaeger traces on `http://localhost:16686` to identify the bottleneck.
2. If vector searches are slow, inspect the Qdrant container logs and consider scaling resources.
3. If LLM generation is slow, consider switching to a fallback provider or a faster model.

### Rate Limits and Circuit Breakers

BaselithCore uses circuit breakers to protect against cascading failures from external dependencies (LLMs, VectorStores).

- **Symptoms of an Open Circuit:** Immediate failures without waiting for a timeout when calling the affected service.
- **Resolution:**
    - Circuit breakers will automatically transition to "HALF-OPEN" state after the configured timeout period and probe the external service.
    - If the service is healthy again, the circuit breaker resets to "CLOSED".
    - If issues persist, check the provider's status page.

## Disaster Recovery

### Backups

- **Docker Compose:** `scripts/backup-db.sh` runs `pg_dump | gzip` to
  `/backups/postgres` and prunes dumps older than 30 days. Schedule it with cron.
- **Kubernetes:** enable the chart's backup CronJob — `backup.enabled=true`
  (daily 02:00 by default, configurable `backup.schedule` / `backup.retentionDays`).
  Point `backup.volume` at a PVC and sync it offsite (object storage) for true DR.

### Verify backups (don't trust untested backups)

```bash
# Fast integrity check (gzip CRC + non-empty + SQL sanity)
./scripts/verify-backup.sh /backups/postgres/backup_20260606_020000.sql.gz

# Full restore drill into a throwaway container + sanity query
./scripts/verify-backup.sh /backups/postgres/backup_20260606_020000.sql.gz --drill
```

Run the `--drill` verification at least monthly; it both proves restorability
and measures your real RTO (its wall-clock time).

### RTO / RPO targets

| Metric | Target | Driven by |
|---|---|---|
| **RPO** (max data loss) | ≤ 24h (default) | backup frequency — tighten `backup.schedule` (e.g. hourly) for a smaller RPO |
| **RTO** (time to restore) | ≤ 1h | measured by the restore drill; depends on dump size + provisioning |

For sub-hour RPO, complement dumps with Postgres WAL archiving / PITR or a
managed Postgres with continuous backup.

### Restore procedure (production)

1. Provision/clear the target Postgres.
2. `./scripts/restore-db.sh <backup.sql.gz>` (Compose) or pipe the dump into
   `psql` against the managed instance.
3. Run Alembic migrations if the dump predates the current schema:
   `baselith db upgrade` (or `alembic upgrade head`).
4. Verify readiness: `GET /health/ready` returns 200.

## Routine Maintenance

- Keep the system updated with latest security patches.
- Periodically review Prometheus metrics and adjust alert thresholds as needed.
- Run `./scripts/verify-backup.sh --drill` monthly; full restore rehearsal quarterly.
