# Development & Refactoring Log

This log tracks significant changes, completed phases, and architectural decisions made during the development of BaselithCore.

## [2026-03-12] Production Readiness Enhancements

### Completed Phase: Operational Hardening

As part of the production readiness initiative, critical infrastructure gaps were identified and resolved.

**Key Decisions & Changes:**

- **Database Migrations (Alembic):** Replaced manual `CREATE TABLE IF NOT EXISTS` logic with a formal migration system.
    - Decision: Use Alembic with `sqlalchemy.ext.asyncio` and `psycopg_async` to maintain the "Async by Default" rule.
    - Implementation: Automatic `alembic upgrade head` execution during the FastAPI lifespan.
- **Error Tracking (Sentry):** Integrated Sentry SDK for real-time error reporting and performance monitoring.
    - Implementation: Initialized within the application lifecycle (`lifespan`) to ensure modularity.
- **Automated Backups:** Implemented `scripts/backup-db.sh` with a 30-day retention policy for PostgreSQL dumps.
- **Production Infrastructure:** Created `docker-compose.prod.yml` with strict resource limits, health checks, and optimized service isolation.

---
