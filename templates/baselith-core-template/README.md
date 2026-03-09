# Baselith-Core Project Template

Standard project structure for building production-ready applications with BaselithCore.

## Features

- **Core Orchestration**: Pre-configured `backend.py` with standard middleware.
- **Service Layer**: Initialization for LLM, Graph, and Vector services.
- **Security Defaults**: Pre-configured JWT, Rate Limiting, and Tenant Isolation.
- **Observability**: Structured logging and metrics endpoints.
- **Modular Architecture**: Separate `core/`, `plugins/`, and `configs/`.

## Quick Start

```bash
# 1. Initialize project
baselith init my-new-project --template baselith-core

# 2. Setup environment
cd my-new-project
cp .env.example .env

# 3. Start development server
npm run dev # or python backend.py
```

## Structure

```txt
my-new-project/
├── core/                # Agnostic logic
├── plugins/             # Domain-specific plugins
├── configs/             # YAML configurations
├── data/                # Persistent storage (local)
├── backend.py           # FastAPI entry point
└── requirements.txt     # Python dependencies
```

## Configuration

Edit `configs/settings.yaml` to customize the system behavior. Environment variables in `.env` take precedence.

## Deployment

Refer to `docs/deployment.md` for Docker and production guidelines.
