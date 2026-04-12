#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/configs/.env.production}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.prod.yml}"

required_vars=(
  DB_HOST
  DB_PASSWORD
  SECRET_KEY
  SANDBOX_DOCKER_HOST
  SANDBOX_CERTS_DIR
)

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo ">> $*"
}

[[ -f "$ENV_FILE" ]] || fail "Env file not found: $ENV_FILE"
[[ -f "$COMPOSE_FILE" ]] || fail "Compose file not found: $COMPOSE_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

for var_name in "${required_vars[@]}"; do
  [[ -n "${!var_name:-}" ]] || fail "Required variable missing: $var_name"
done

[[ ${#SECRET_KEY} -ge 32 ]] || fail "SECRET_KEY must be at least 32 characters"

CERTS_DIR="${SANDBOX_CERTS_DIR}"
[[ -d "$CERTS_DIR" ]] || fail "Sandbox cert directory not found: $CERTS_DIR"

for cert_file in ca.pem cert.pem key.pem; do
  [[ -f "$CERTS_DIR/$cert_file" ]] || fail "Missing sandbox client cert: $CERTS_DIR/$cert_file"
done

info "Rendering production compose"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null

SANDBOX_HOST="${SANDBOX_DOCKER_HOST%:*}"
SANDBOX_PORT="${SANDBOX_DOCKER_HOST##*:}"

if [[ "$SANDBOX_HOST" == "$SANDBOX_PORT" ]]; then
  fail "SANDBOX_DOCKER_HOST must include host:port"
fi

info "Checking external sandbox TCP reachability: $SANDBOX_DOCKER_HOST"
if command -v nc >/dev/null 2>&1; then
  nc -z "$SANDBOX_HOST" "$SANDBOX_PORT" || fail "Cannot reach sandbox host $SANDBOX_DOCKER_HOST"
else
  bash -c "exec 3<>/dev/tcp/$SANDBOX_HOST/$SANDBOX_PORT" \
    || fail "Cannot reach sandbox host $SANDBOX_DOCKER_HOST"
fi

info "Checking Docker TLS handshake against external sandbox"
docker \
  --host "tcp://${SANDBOX_DOCKER_HOST}" \
  --tlsverify \
  --tlscacert "$CERTS_DIR/ca.pem" \
  --tlscert "$CERTS_DIR/cert.pem" \
  --tlskey "$CERTS_DIR/key.pem" \
  version >/dev/null

info "Production preflight passed"
