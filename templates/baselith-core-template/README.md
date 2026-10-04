# {project_name}

A BaselithCore API server: the framework's FastAPI app, ready for your plugins.

## Quick start

```bash
pip install -r requirements.txt
ollama pull llama3.2    # the local model LLM_MODEL names (https://ollama.com)
baselith run            # API on http://127.0.0.1:8000 — try /health and /docs
```

`baselith run` checks the configuration first and refuses to start while the
model is missing; it does not need the database or Redis.

`baselith init` generated `.env` for local development: `APP_ENV=development`,
a random `SECRET_KEY` and `DB_PASSWORD` for this project only (file mode 0600,
ignored by git), loopback-only `HOST` and `TRUSTED_HOSTS`, and
`LLM_PROVIDER=ollama`, the provider that needs no API key.

The server starts without any backing service and reports what is missing on
`/health/ready`. To run the full stack locally:

```bash
docker compose up -d    # PostgreSQL, Redis (FalkorDB), Qdrant on localhost
baselith doctor         # check configuration and services
```

## Layout

```txt
{project_name}/
├── backend.py          # The API server (`baselith run` serves backend:app)
├── plugins/            # Your plugins — domain logic belongs here
├── data/               # Local data (generated, git-ignored)
├── docker-compose.yml  # Local PostgreSQL / Redis / Qdrant
├── .env                # Local configuration (generated, never commit it)
└── requirements.txt
```

Add a plugin with `baselith plugin create <name>`; enable a bundled one with
`baselith plugin enable <name>`.

## Deployment

`.env` is a development profile. A production deployment sets
`APP_ENV=production`, its own secrets, `TRUSTED_HOSTS` for its public hostname
and reachable services — `baselith doctor` reports what is missing.
