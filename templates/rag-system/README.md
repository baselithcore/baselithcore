# RAG System Template

A complete Retrieval-Augmented Generation (RAG) system template.

## Features

- **Document Ingestion**: Ingest text into the vector store
- **Semantic Search**: Query-based retrieval with configurable relevance
- **LLM Integration**: any provider the framework supports (Ollama by default)
- **API**: FastAPI REST endpoints for all operations

## Quick Start

```bash
baselith init my-rag-project --template rag-system
cd my-rag-project
pip install -r requirements.txt
docker compose up -d    # Qdrant and Redis on localhost
ollama pull llama3.2    # the model LLM_MODEL names
python main.py          # API on http://127.0.0.1:8000 — try /health and /docs
```

`baselith init` generated `.env` for local development: `APP_ENV=development`,
a random `SECRET_KEY` for this project only (file mode 0600, ignored by git),
loopback-only `HOST`/`PORT` (which `main.py` binds), the Qdrant and Redis
endpoints on localhost, and `LLM_PROVIDER=ollama`, the provider that needs no
API key. The server starts without Qdrant or the model; `/ingest` and `/query`
answer 503 until they are reachable.

## Architecture

```text
┌─────────────────┐     ┌───────────────┐     ┌────────────────┐
│   Documents     │────▶│   Ingestion   │────▶│  Vector Store  │
│  (PDF/TXT/MD)   │     │   Pipeline    │     │    (Qdrant)    │
└─────────────────┘     └───────────────┘     └────────────────┘
                                                      │
┌─────────────────┐     ┌───────────────┐             │
│      User       │────▶│   Query API   │◀────────────┘
│     Query       │     │   (FastAPI)   │
└─────────────────┘     └───────────────┘
                               │
                        ┌──────▼──────┐     ┌────────────────┐
                        │  RAG Agent  │────▶│  LLM Provider  │
                        │             │     │(Ollama/OpenAI) │
                        └─────────────┘     └────────────────┘
```

## Configuration

Everything is read from `.env` by the framework's settings classes:

| Setting | Development value | Purpose |
|---------|-------------------|---------|
| `LLM_PROVIDER` / `LLM_MODEL` | `ollama` / `llama3.2` | Answer generation; set `LLM_API_KEY` for a hosted provider |
| `VECTORSTORE_QDRANT_HOST` / `VECTORSTORE_PORT` | `localhost` / `6333` | Qdrant endpoint |
| `VECTORSTORE_COLLECTION_NAME` | `documents` (default) | Collection used when a request names none |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Where `python main.py` listens |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/ingest` | POST | Upload documents |
| `/query` | POST | Query the knowledge base |
