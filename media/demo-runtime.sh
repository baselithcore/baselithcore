#!/usr/bin/env bash
#
# Scripted terminal demo for the README: bring the whole runtime up.
#
# Shows `baselith setup docker-core`, the Compose stack reaching a healthy
# state, and the API answering on /health.
#
#   python media/record.py 100 30 \
#       asciinema rec --overwrite -c "./media/demo-runtime.sh" media/demo-runtime.cast
#   agg --font-size 18 --speed 2 --idle-time-limit 0.8 --fps-cap 10 \
#       media/demo-runtime.cast media/demo-runtime.gif
#
# Record it with the images already pulled and the API image already built —
# a cold first build takes minutes and belongs in the docs, not in a GIF. Do a
# full `baselith up` once by hand before recording.
#
# Set DEMO_TEARDOWN=0 to leave the stack running when the demo ends. When the
# default host ports are taken by another stack, export BASELITH_HTTP_PORT,
# BASELITH_POSTGRES_PORT, BASELITH_REDIS_PORT or BASELITH_QDRANT_PORT before
# recording — the commands shown stay the same, only the published ports move.

set -o pipefail

ENV_FILE=configs/.env.docker.core
COMPOSE_FILE=docker-compose.core.yml
TEARDOWN=${DEMO_TEARDOWN:-1}
TYPING_DELAY=0.035
PROMPT=$'\033[1;36m\xe2\x9d\xaf\033[0m '

command -v docker >/dev/null 2>&1 || {
    echo "docker is required." >&2
    exit 1
}
command -v baselith >/dev/null 2>&1 || {
    echo "baselith is not on PATH — activate the environment that provides it." >&2
    exit 1
}
[[ -f $COMPOSE_FILE ]] || {
    echo "run this from the repository root." >&2
    exit 1
}

export BASELITH_DOCKER_ENV_FILE="$ENV_FILE"
HTTP_PORT=${BASELITH_HTTP_PORT:-8000}

clear 2>/dev/null || printf '\033[2J\033[H'

type_line() {
    local line=$1 i
    printf '%s' "$PROMPT"
    for ((i = 0; i < ${#line}; i++)); do
        printf '%s' "${line:i:1}"
        sleep "$TYPING_DELAY"
    done
    printf '\n'
}

run() {
    type_line "$1"
    eval "$1"
    printf '\n'
    sleep "${2:-1.5}"
}

sleep 1
run "baselith setup docker-core" 2
run "docker compose --env-file $ENV_FILE -f $COMPOSE_FILE up -d --wait" 2
run "curl -s http://localhost:$HTTP_PORT/health | jq -c" 3

if [[ $TEARDOWN == 1 ]]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down >/dev/null 2>&1
fi
