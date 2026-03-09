# Configuration Templates

This directory contains the **official configuration templates** for the Baselith-Core.

> **Note**: Legacy configuration files (`.env.example`, `.env.prod`, `plugins.yaml`, etc.) have been removed from the project root. The framework now exclusively uses the templates in this directory.

## File Structure

| File                   | Purpose                                                            |
| ---------------------- | ------------------------------------------------------------------ |
| `.env.base`            | **Complete reference** with all settings and defaults. Start here. |
| `.env.development`     | Settings for local development (Ollama, debug enabled).            |
| `.env.production`      | Settings for production (OpenAI, Redis, strict security).          |
| `.env.test`            | Settings for automated tests (isolation, caching disabled).        |
| `plugins.yaml.example` | Granular plugin configuration example.                             |

## Quick Start

```bash
# For development
cp configs/.env.base .env
# Optional: apply development overrides
cat configs/.env.development >> .env

# For production
cp configs/.env.base .env
cat configs/.env.production >> .env
```

## Configuration Categories

The framework uses semantic prefixes to organize settings:

### Core Framework

| Prefix         | Purpose                                                |
| -------------- | ------------------------------------------------------ |
| `CORE_`        | Framework settings (logging, directories, workers)     |
| `LLM_`         | Language model providers (OpenAI, Ollama, HuggingFace) |
| `VECTORSTORE_` | Vector database (Qdrant)                               |
| `CHAT_`        | Chat service and RAG pipeline                          |

### Advanced Services

| Prefix      | Purpose                           |
| ----------- | --------------------------------- |
| `VISION_`   | Vision/image analysis service     |
| `VOICE_`    | Text-to-speech and speech-to-text |
| `FINETUNE_` | LLM fine-tuning service           |
| `EVAL_`     | Evaluation and LLM-as-a-Judge     |

### Infrastructure

| Prefix              | Purpose                      |
| ------------------- | ---------------------------- |
| `DB_` / `POSTGRES_` | PostgreSQL database          |
| `GRAPH_`            | Graph database (FalkorDB)    |
| `CACHE_`            | Caching layer (Memory/Redis) |
| `SANDBOX_`          | Code execution sandbox       |

### Reasoning & Resilience

| Prefix        | Purpose                            |
| ------------- | ---------------------------------- |
| `TOT_`        | Tree of Thoughts reasoning         |
| `RESILIENCE_` | Circuit breakers and rate limiting |

### Processing

| Prefix            | Purpose                        |
| ----------------- | ------------------------------ |
| `DOCUMENTS_`      | Document processing extensions |
| `WEB_`            | Web document crawling          |
| `SPACY_` / `PDF_` | NLP and OCR processing         |
| `CHANDRA_`        | Advanced OCR configuration     |

### Application Logic

| Prefix     | Purpose                    |
| ---------- | -------------------------- |
| `APP_`     | Application-level settings |
| `CHATBOT_` | Widget UI customization    |
| `PLUGIN_`  | Plugin system behavior     |

### Security

| Prefix       | Purpose                 |
| ------------ | ----------------------- |
| `AUTH_`      | Authentication settings |
| `API_KEYS_`  | API key management      |
| `ADMIN_`     | Admin panel credentials |
| `SECRET_KEY` | Cryptographic secret    |

## Pydantic Integration

These files map directly to Pydantic config classes in `core/config/`:

```text
configs/.env.base  →  core/config/services.py   (LLMConfig, ChatConfig, etc.)
                  →  core/config/storage.py    (StorageConfig)
                  →  core/config/sandbox.py    (SandboxConfig)
                  →  core/config/reasoning.py  (ReasoningConfig)
                  →  core/config/resilience.py (ResilienceConfig)
```

Any changes here must reflect (or derive from) the Pydantic models.

---

> **Security Warning**: Never commit secrets (API keys, passwords) to these template files. Use environment variables or a secrets manager in production.
