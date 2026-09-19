#!/usr/bin/env sh
set -eu

mkdir -p \
  "${CORE_DATA_DIR:-/app/data}" \
  "${CORE_DOCUMENTS_DIR:-/app/documents}" \
  "${CORE_PLUGIN_DIR:-/app/plugins}" \
  /app/logs

if [ "${BASELITH_RUN_MIGRATIONS:-true}" = "true" ]; then
  baselith db migrate
fi

exec uvicorn backend:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --proxy-headers \
  --no-server-header \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --timeout-graceful-shutdown "${GRACEFUL_SHUTDOWN_TIMEOUT:-25}" \
  --timeout-keep-alive "${UVICORN_KEEP_ALIVE:-75}" \
  ${UVICORN_LIMIT_CONCURRENCY:+--limit-concurrency "$UVICORN_LIMIT_CONCURRENCY"}
