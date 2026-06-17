# Dev Container

One-command onboarding for BaselithCore. Open the repo in a
[Dev Container](https://containers.dev) (VS Code "Reopen in Container", GitHub
Codespaces, or any devcontainer-compatible tool) and the environment builds
itself.

## What you get

- **Python 3.12** with the framework installed editable plus all dev tooling
  (`pip install -e ".[dev]"`) and pre-commit hooks.
- **Node LTS** for the TypeScript SDK (`sdk/typescript`) and the dashboard UIs.
- **Docker access** (docker-outside-of-docker) so `docker compose up -d` brings
  up Redis, Qdrant, and Postgres from the repo's `docker-compose.yml`.
- VS Code wired for Ruff (format + organize imports on save), Mypy, pytest,
  Docker, TOML, and YAML.

## After it opens

`postCreateCommand` runs [`post-create.sh`](post-create.sh) automatically. Then:

```bash
docker compose up -d     # supporting services
baselith doctor          # verify configuration
python backend.py        # API on :8000  →  /console, /docs
```

Forwarded ports: **8000** (API), 5432 (Postgres), 6379 (Redis), 6333 (Qdrant).
