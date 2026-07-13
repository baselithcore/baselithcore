---
title: Installation
description: Complete installation guide for the BaselithCore Framework
---
<!-- markdownlint-disable MD046 -->

This guide walks you through installing and configuring the framework.

---

## System Requirements

### Required

| Component | Version | Verification Command  |
| --------- | ------- | --------------------- |
| Python    | 3.12+   | `python --version`    |
| pip       | 21+     | `pip --version`       |
| FalkorDB  | latest  | `redis-cli ping`      |

### Recommended

| Component  | Version | Purpose                |
| ---------- | ------- | ---------------------- |
| PostgreSQL | 14+     | Structured persistence |
| Qdrant     | 1.6+    | Vector store for RAG   |
| Ollama     | Latest  | Local LLM              |

---

## Step-by-Step Installation

### Option A: Install via pip (Recommended for users)

```bash
pip install baselith-core
```

Install optional capabilities as needed:

```bash
# RAG / embeddings / reranking
pip install "baselith-core[rag]"

# Browser automation and JS rendering
pip install "baselith-core[browser,web]"

# Document ingestion, OCR, and spaCy enrichment
pip install "baselith-core[documents,ocr,nlp]"

# High-fidelity OCR via MinerU (heavy: torch stack; models download on first use)
pip install "baselith-core[mineru]"

# Hugging Face inference/local provider support
pip install "baselith-core[huggingface]"
```

### Option B: Clone and Install (Recommended for developers)

```bash
git clone https://github.com/baselithcore/baselithcore.git
cd baselithcore
```

### 2. Set Up Virtual Environment

=== "Linux/macOS"

    ```bash
    python -m venv .venv
    source .venv/bin/activate
    ```

=== "Windows"

    ```powershell
    python -m venv .venv
    .venv\Scripts\activate
    ```

### 3. Install Dependencies

```bash
# Base installation
pip install -e .

# With development dependencies
pip install -e ".[dev]"

# With selected optional capabilities
pip install -e ".[rag,browser,web]"
pip install -e ".[documents,ocr,nlp]"
pip install -e ".[mineru]"
pip install -e ".[huggingface]"
```

### 4. Environment Configuration

Copy the example configuration file:

```bash
cp .env.example .env
```

Edit `.env` with your configurations:

```env title=".env"
# === Core Settings ===
CORE_DEBUG=true
CORE_LOG_LEVEL=INFO

# === LLM Configuration ===
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2
LLM_API_BASE=http://localhost:11434

# === Storage Configuration ===
POSTGRES_ENABLED=true
DB_HOST=localhost
DB_NAME=baselithcore
DB_USER=baselithcore
DB_PASSWORD=change-me-before-production

# Vector Store
VECTORSTORE_PROVIDER=qdrant
VECTORSTORE_HOST=localhost
VECTORSTORE_PORT=6333

# Cache & Queue (FalkorDB / Redis)
CACHE_BACKEND=redis
CACHE_REDIS_URL=redis://localhost:6379/1
QUEUE_REDIS_URL=redis://localhost:6379/2

# === Observability ===
TELEMETRY_ENABLED=true
TELEMETRY_OTEL_ENDPOINT=http://localhost:4317

# === Marketplace ===
# URL for discovering and downloading plugins (can be overriden for local mirrors)
MARKETPLACE_CENTRAL_URL=https://marketplace.baselithcore.xyz/api/marketplace/plugins/registry.json
# URL for official authentication and portal
MARKETPLACE_AUTH_URL=https://marketplace.baselithcore.xyz

!!! note "Security Restriction"
    For security reasons, the plugin **publishing** destination is hardcoded to the official marketplace and cannot be overridden by `MARKETPLACE_CENTRAL_URL`.
    Marketplace plugin installation accepts only `https` repository URLs; non-HTTPS sources are rejected by the installer.

# === Security ===
SECRET_KEY=your-secret-key-change-in-production
ALLOW_ORIGINS=["*"]
```

---

## FalkorDB Configuration (Redis-Compatible)

The system uses FalkorDB (or Redis) for three distinct purposes, each on a separate database:

The target database for each role is encoded in its connection URL (the trailing
`/<db-number>`), configured via dedicated environment variables:

| Database | Purpose                    | Environment variable                          |
| -------- | -------------------------- | --------------------------------------------- |
| DB 0     | Knowledge Graph (FalkorDB) | `GRAPH_DB_URL=redis://localhost:6379`         |
| DB 1     | Caching                    | `CACHE_REDIS_URL=redis://localhost:6379/1`    |
| DB 2     | Task Queue (RQ)            | `QUEUE_REDIS_URL=redis://localhost:6379/2`    |

!!! info "FalkorDB Installation"
    FalkorDB is a Redis fork that provides graph capabilities. It is used for the Knowledge Graph and as a compatible engine for Caching and Task Queues.

    === "macOS & Linux (Docker - Recommended)"
        ```bash
        docker run -d -p 6379:6379 falkordb/falkordb:latest
        ```

    === "Linux (Native)"
        Download the latest binary from the [FalkorDB Releases](https://github.com/FalkorDB/FalkorDB/releases) or build it from source.

    === "Docker Compose"
        The included `docker-compose.yml` automatically includes FalkorDB.

---

## LLM Configuration

The framework supports multiple LLM providers:

### Ollama (Local)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Download a model
ollama pull llama3.2

# Verify
ollama list
```

Configuration in `.env`:

```env
LLM_PROVIDER=ollama
LLM_API_BASE=http://localhost:11434
LLM_MODEL=llama3.2
```

### OpenAI (Cloud)

```env
LLM_PROVIDER=openai
LLM_OPENAI_API_KEY=sk-your-api-key
LLM_MODEL=gpt-4o-mini
```

### HuggingFace (Inference API)

```env
LLM_PROVIDER=huggingface
LLM_API_KEY=hf_your-api-key
LLM_MODEL=mistralai/Mistral-7B-Instruct-v0.2
```

---

## Installation Verification

Run the diagnostic command:

```bash
baselith doctor
```

Expected output:

```text
✅ BaselithCore Health Check
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ Python version: 3.12+ (OK)
✅ Dependencies: All installed
✅ Redis (Cache): Connected (localhost:6379)
✅ Qdrant: Connected (localhost:6333)
✅ GraphDB: Connected (localhost:6379)
✅ LLM Provider: Ollama (llama3.2)
✅ Configuration: Valid

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: Ready ✅
```

---

## Troubleshooting

??? failure "FalkorDB connection refused"
    Verify that FalkorDB is running:
    ```bash
    redis-cli ping
    # Expected output: PONG
    ```

    If it doesn't respond, start the FalkorDB container:
    ```bash
    docker start baselith-core-redis
    ```

??? failure "LLM provider not configured"
    Verify the configuration in `.env`:
    ```bash
    # For Ollama
    curl <http://localhost:11434/api/tags>

    # For OpenAI
    echo $OPENAI_API_KEY
    ```

??? failure "Import errors"
    Ensure you installed in editable mode:
    ```bash
    pip install -e .
    ```

??? failure "Permission denied errors"
    On Linux/macOS, you may need to adjust permissions:
    ```bash
    chmod +x scripts/*.sh
    ```

??? failure "Port already in use"
    If port 8000 is occupied, change it in the configuration:
    ```env
    PORT=8001
    ```

---

## Platform-Specific Notes

### macOS

- Use Homebrew for dependency management
- Ensure Xcode Command Line Tools are installed: `xcode-select --install`
- If using Apple Silicon, ensure Redis and other dependencies have ARM support

### Linux

- Some distributions may require `python3-venv`: `sudo apt install python3-venv`
- For systemd service management, see [Deployment Guide](../advanced/deployment.md)

### Windows

- Use PowerShell or Windows Terminal
- Consider using Windows Subsystem for Linux (WSL2) for better compatibility
- Redis requires WSL or Docker on Windows

---

---

## Shell Completion

The `baselith` CLI supports autocompletion for Bash and ZSH via `argcomplete`.

### 1. Installation

`argcomplete` is a base dependency of `baselith-core`, so it is already installed
with any standard install. If it is somehow missing, install it explicitly:

```bash
pip install argcomplete
```

### 2. Activation

#### Temporary (Current session)

=== "Bash"

    ```bash
    eval "$(register-python-argcomplete baselith)"
    ```

=== "ZSH"

    ```zsh
    autoload -U bashcompinit
    bashcompinit
    eval "$(register-python-argcomplete baselith)"
    ```

#### Permanent

Add the activation command to your shell profile (`~/.bashrc`, `~/.zshrc`, or `~/.bash_profile`).

Alternatively, use the global registration tool:

```bash
activate-global-python-argcomplete --user
```

---

## Next Steps

1. :material-arrow-right: **Launch the Core**: Follow the [Quick Start](quickstart.md) to start the server.
2. :material-monitor: **Open the Portal**: Navigate to `backstage-portal/` and run `yarn start` to explore your agentic ecosystem.
3. :material-puzzle: **Create a Plugin**: Follow the [First Plugin](first-plugin.md) guide to extend the framework.
