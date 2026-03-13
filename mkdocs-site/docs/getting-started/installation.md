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
| Python    | 3.10+   | `python --version`    |
| pip       | 21+     | `pip --version`       |
| FalkorDB  | latest  | `redis-cli --version` |

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

# With all optional dependencies
pip install -e ".[all]"
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
DB_PASSWORD=baselithcore

# Vector Store
VECTORSTORE_PROVIDER=qdrant
VECTORSTORE_HOST=localhost
VECTORSTORE_PORT=6333

# Cache & Queue
CACHE_BACKEND=redis
CACHE_REDIS_URL=redis://localhost:6379/1
QUEUE_REDIS_URL=redis://localhost:6379/2

# === Observability ===
ENABLE_TRACING=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=baselith-core

# === Marketplace ===
MARKETPLACE_CENTRAL_URL=https://registry.baselithcore.xyz/registry.json
MARKETPLACE_AUTH_URL=https://marketplace.baselithcore.xyz

# === Security ===
SECRET_KEY=your-secret-key-change-in-production
ALLOW_ORIGINS=["*"]
```

---

## Redis Configuration

The system uses FalkorDB (or Redis) for three distinct purposes, each on a separate database:

| Database | Purpose                    | Configuration      |
| -------- | -------------------------- | ------------------ |
| DB 0     | Knowledge Graph (FalkorDB) | `REDIS_GRAPH_DB=0` |
| DB 1     | Caching                    | `REDIS_CACHE_DB=1` |
| DB 2     | Task Queue (RQ)            | `REDIS_QUEUE_DB=2` |

!!! info "Redis Installation"
    === "macOS (Homebrew)"
        ```bash
        brew install redis
        brew services start redis
        ```

    === "Ubuntu/Debian"
        ```bash
        sudo apt update
        sudo apt install redis-server
        sudo systemctl start redis
        ```
    
    === "Docker"
        ```bash
        docker run -d -p 6379:6379 redis:7-alpine
        ```

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

✅ Python version: 3.11+ (OK)
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

??? failure "Redis connection refused"
    Verify that Redis is running:
    ```bash
    redis-cli ping
    # Expected output: PONG
    ```

    If it doesn't respond, start Redis:
    ```bash
    redis-server
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
    API_PORT=8001
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

Ensure `argcomplete` is installed (it's included in `dev` and `all` extras):

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

:material-arrow-right: Continue with the [Quick Start](quickstart.md) to launch the system.
