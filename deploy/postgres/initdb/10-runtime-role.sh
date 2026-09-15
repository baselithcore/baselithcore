#!/bin/sh
# Create the least-privilege role the application authenticates as.
#
# PostgreSQL skips a row-level-security policy for a SUPERUSER, for a role with
# BYPASSRLS, and for the *owner* of a table that has no FORCE ROW LEVEL
# SECURITY — which migration 008 deliberately does not set, so that migrations
# and un-tenanted maintenance keep working. POSTGRES_USER is both a superuser
# and the owner of everything Alembic creates, so a stack that runs the app as
# it has DB_RLS_ENABLED=true and no isolation at all. `core.db.rls_posture`
# refuses to boot past that in production; this script is the remedy.
#
# Runs from the postgres image's entrypoint, which executes
# /docker-entrypoint-initdb.d/* ONLY on a fresh data directory. An existing
# volume never sees it — provision the role by hand there (the same SQL) or
# start from a new volume.
#
# No-op unless DB_RUNTIME_USER and DB_RUNTIME_PASSWORD are both set, and unless
# the runtime role differs from the owner: with the defaults this file changes
# nothing.
set -eu

if [ -z "${DB_RUNTIME_USER:-}" ] || [ -z "${DB_RUNTIME_PASSWORD:-}" ]; then
	echo "[runtime-role] DB_RUNTIME_USER/DB_RUNTIME_PASSWORD unset — the app will"
	echo "[runtime-role] connect as ${POSTGRES_USER}, which bypasses row-level security."
	exit 0
fi

if [ "${DB_RUNTIME_USER}" = "${POSTGRES_USER}" ]; then
	echo "[runtime-role] DB_RUNTIME_USER equals POSTGRES_USER (${POSTGRES_USER}):"
	echo "[runtime-role] the app would still connect as the table owner. Skipping."
	exit 0
fi

echo "[runtime-role] provisioning ${DB_RUNTIME_USER} (NOSUPERUSER NOBYPASSRLS)"

# The password reaches psql through `printenv` inside the script, never as an
# argument: a value in argv is readable by any process via /proc/<pid>/cmdline.
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<'SQL'
\set role_name `printenv DB_RUNTIME_USER`
\set role_pw `printenv DB_RUNTIME_PASSWORD`
\set db_name `printenv POSTGRES_DB`

CREATE ROLE :"role_name" LOGIN PASSWORD :'role_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;

GRANT CONNECT ON DATABASE :"db_name" TO :"role_name";
GRANT USAGE ON SCHEMA public TO :"role_name";

-- This runs before Alembic, so there is nothing to GRANT ON ALL TABLES yet.
-- ALTER DEFAULT PRIVILEGES is what makes it work: every table and sequence the
-- owner creates later — i.e. every migration — is granted to the runtime role
-- as it is created. DML only: DDL stays with the owner, which is exactly what
-- keeps the runtime role out of the RLS-exempt "table owner" category.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"role_name";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"role_name";
SQL

echo "[runtime-role] done — point the app at it with DB_USER=${DB_RUNTIME_USER}"
