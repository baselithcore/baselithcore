# Research Assistant

BaselithCore-powered research assistant for scientific paper analysis and citation management.

## Features

- **Paper Analysis**: Extract abstract, keywords, and references.
- **Citation Graph**: Map relationships between papers.
- **Semantic Search**: Find related papers via embeddings.
- **Multi-Paper Synthesis**: Generate cross-document summaries.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start
python main.py

# 3. Add paper
curl -X POST http://localhost:8002/papers \
  -F "file=@paper.pdf"

# 4. Search
curl "http://localhost:8002/search?query=machine+learning"
```

## Structure

```txt
research-assistant/
├── main.py              # Entry point
├── plugin.py            # Plugin implementation
└── requirements.txt
```

## API Endpoints

- `POST /papers` - Upload a paper.
- `GET /papers` - List saved papers.
- `GET /search` - Semantic search.
- `GET /citations/{paper_id}` - Citation graph.
- `POST /synthesize` - Generate multi-paper synthesis.

## Plugin Example

```python
class ResearchPlugin(AgentPlugin):
    name = "research-assistant"
    
    def register_entity_types(self):
        return [
            {"type": "paper", "display": "Paper"},
            {"type": "author", "display": "Author"},
            {"type": "keyword", "display": "Keyword"},
        ]
    
    def register_relationship_types(self):
        return [
            {"type": "CITES", "source": ["paper"], "target": ["paper"]},
            {"type": "AUTHORED_BY", "source": ["paper"], "target": ["author"]},
        ]
```

## Use Cases

1. **Literature Review**: Upload papers and generate automatic syntheses.
2. **Citation Analysis**: Visualize impact and connections.
3. **Research Discovery**: Find related papers not found in direct citations.
