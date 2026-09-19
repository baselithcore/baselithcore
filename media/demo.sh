#!/usr/bin/env bash
#
# Scripted terminal demo for the README: scaffold, inspect and validate a plugin.
#
# Record and render it from the repository root, with the project environment
# active (the one that provides the `baselith` CLI):
#
#   python media/record.py 100 30 \
#       asciinema rec --overwrite -c "./media/demo.sh" media/demo.cast
#   agg --font-size 18 --speed 1.3 --idle-time-limit 1 --fps-cap 10 \
#       media/demo.cast media/demo.gif
#
# The demo touches nothing tracked: the plugin is created with --no-register,
# so configs/plugins.yaml is never rewritten, and plugins/my_plugin is removed
# before and after the run.

set -uo pipefail

PLUGIN=my_plugin
PROMPT=$'\033[1;36m\xe2\x9d\xaf\033[0m '
TYPING_DELAY=0.035

if ! command -v baselith >/dev/null 2>&1; then
    echo "baselith is not on PATH — activate the project environment first." >&2
    exit 1
fi

cleanup() {
    rm -rf "plugins/${PLUGIN}"
}

# Start from a clean slate so the recording is repeatable.
cleanup
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
run "baselith plugin create ${PLUGIN} --type agent --no-register" 1.8
run "ls plugins/${PLUGIN}" 1.8
run "baselith plugin validate ${PLUGIN}" 3

cleanup
