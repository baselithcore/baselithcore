# NLP Utilities

The `core/nlp/` module provides Natural Language Processing utilities built on **spaCy**, with graceful degradation when the spaCy library or models are unavailable.

## Module Structure

```yaml
core/nlp/
├── spacy_utils.py   # Lazy-loaded spaCy pipeline with fallback
└── models.py        # Embedding model loader (sentence-transformers)
```

---

## spaCy Pipeline

The spaCy integration uses a **lazy, cached loader** — the model is only loaded on first use and cached for the lifetime of the process.

```python
from core.nlp.spacy_utils import get_spacy_pipeline, is_spacy_available, extract_spacy_metadata

# Check availability without triggering a load
if is_spacy_available():
    # Extract metadata from text
    metadata = extract_spacy_metadata("BaselithCore is a Python framework built in 2025.")
    print(metadata)
    # {
    #   "spacy_language": "en",
    #   "spacy_model": "en_core_web_sm",
    #   "spacy_token_count": "11",
    #   "spacy_sentence_count": "1",
    #   "spacy_entities": "BaselithCore (ORG); 2025 (DATE)"
    # }
```

### Fallback Behaviour

If the configured spaCy model is unavailable, the module falls back gracefully:

1. Tries to load the configured model (`spacy_model` in config)
2. If unavailable → creates a blank `Language` pipeline with `sentencizer`
3. If spaCy is not installed at all → returns `None` from `get_spacy_pipeline()`

No exceptions are raised in any case.

---

## Configuration

```bash
ENABLE_SPACY_DOCUMENTS=true       # Enable spaCy enrichment during document ingestion
SPACY_MODEL=en_core_web_sm        # spaCy model to load
SPACY_FALLBACK_LANGUAGE=en        # Language for blank fallback pipeline
```

Install spaCy and a model:

```bash
pip install spacy
python -m spacy download en_core_web_sm
```

---

## Embedding Models

```python
from core.nlp.models import get_embedder, get_reranker

# Load sentence-transformers embedder (cached)
embedder = get_embedder("sentence-transformers/all-MiniLM-L6-v2")
embeddings = embedder.encode(["text one", "text two"])

# Load cross-encoder reranker (cached)
reranker = get_reranker("cross-encoder/ms-marco-MiniLM-L-6-v2")
scores = reranker.predict([("query", "doc1"), ("query", "doc2")])
```

!!! tip "Performance"
    Both `get_spacy_pipeline()` and `get_embedder()` use `@lru_cache(maxsize=1)` — models are loaded once and reused across all requests. Thread-safe by design.
