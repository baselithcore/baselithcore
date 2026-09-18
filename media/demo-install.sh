#!/usr/bin/env bash
#
# Scripted terminal demo for the README: install BaselithCore from PyPI and
# bootstrap a project with the CLI.
#
# Record and render it from anywhere, with no project environment active — the
# point is that a bare Python 3.12 plus uv is enough. Set DEMO_INSTALLER=pip to
# record the same demo through pip instead.
#
#   python media/record.py 100 30 \
#       asciinema rec --overwrite -c "./media/demo-install.sh" media/demo-install.cast
#   agg --font-size 16 --speed 6 --idle-time-limit 0.6 --fps-cap 6 \
#       media/demo-install.cast media/demo-install.gif
#
# The demo runs entirely in /tmp/baselith-demo, which it recreates on every run,
# so it touches neither this repository nor any environment you care about.

# No `set -u`: the venv activation script reads unset variables by design.
set -o pipefail

DEMO_DIR=/tmp/baselith-demo
TYPING_DELAY=0.035
PROMPT=$'\033[1;36m\xe2\x9d\xaf\033[0m '

INSTALLER=${DEMO_INSTALLER:-uv}

command -v python3 >/dev/null 2>&1 || {
    echo "python3 is required." >&2
    exit 1
}

if [[ $INSTALLER == uv ]] && ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed — set DEMO_INSTALLER=pip, or see https://docs.astral.sh/uv/" >&2
    exit 1
fi

# Keep pip's own upgrade notice out of the recording.
export PIP_DISABLE_PIP_VERSION_CHECK=1

rm -rf "$DEMO_DIR"
mkdir -p "$DEMO_DIR"
cd "$DEMO_DIR" || exit 1
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
if [[ $INSTALLER == uv ]]; then
    run "uv venv --python 3.12 && source .venv/bin/activate" 1
    run "uv pip install baselith-core" 1.5
else
    run "python3 -m venv .venv && source .venv/bin/activate" 1
    run "pip install baselith-core" 1.5
fi
run "baselith --version" 1.5
run "baselith init my-agent --template minimal" 2.5
run "cd my-agent && ls -1" 3
