# RAG System Template

A complete Retrieval-Augmented Generation (RAG) system template.

## Features

- **Document Ingestion**: Ingest PDF, TXT, MD files into vector store
- **Semantic Search**: Query-based retrieval with configurable relevance
- **LLM Integration**: OpenAI, Ollama, Azure OpenAI support
- **Memory**: Conversation history and context management
- **API**: FastAPI REST endpoints for all operations

## Quick Start

```bash
# Copy template
cp -r templates/rag-system my-rag-project
cd my-rag-project

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Start services
docker-compose up -d

# Run the application
python main.py
```

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

Edit `config.yaml`:

```yaml
llm:
  provider: ollama  # or openai, azure
  model: llama3.1:8b
  temperature: 0.7

vectorstore:
  provider: qdrant
  collection: documents
  embedding_model: all-MiniLM-L6-v2

ingestion:
  chunk_size: 512
  chunk_overlap: 50
  supported_formats: [pdf, txt, md, docx]

retrieval:
  top_k: 5
  score_threshold: 0.7
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/ingest` | POST | Upload documents |
| `/query` | POST | Query the knowledge base |
| `/collections` | GET | List collections |
| `/documents` | GET | List ingested documents |
