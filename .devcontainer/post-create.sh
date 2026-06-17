#!/usr/bin/env bash
# One-time setup after the dev container is created.
set -euo pipefail

echo "▸ Installing BaselithCore with dev tooling…"
python -m pip install --upgrade pip
pip install -e ".[dev]"

echo "▸ Installing pre-commit hooks…"
pre-commit install || echo "  (pre-commit not configured — skipping)"

cat <<'EOF'

✅ BaselithCore dev environment ready.

Next steps:
  docker compose up -d        # Redis, Qdrant, Postgres (docker-outside-of-docker)
  baselith doctor             # verify configuration
  python backend.py           # start the API on :8000  →  /console, /docs

Tests:    python -m pytest -m "not slow"
Lint:     ruff check . && mypy core/
EOF
