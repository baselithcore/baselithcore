# Document Analyzer

BaselithCore-powered system for document analysis with entity and relationship extraction.

## Features

- **Entity Extraction**: Automatically identify persons, organizations, dates, and locations.
- **Relationship Analysis**: Map connections between entities.
- **Knowledge Graph**: Visualize the extracted knowledge graph.
- **Structured Reports**: Generate reports in JSON/Markdown format.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp config.example.yaml config.yaml

# 3. Start
python main.py

# 4. Analyze a document
curl -X POST http://localhost:8000/analyze \
  -F "file=@document.pdf"
```

## Structure

```txt
document-analyzer/
├── main.py              # Entry point
├── config.yaml          # Configuration
├── plugin.py            # Plugin implementation
└── requirements.txt
```

## API Endpoints

- `POST /analyze` - Analyze a document and return entities.
- `GET /entities` - List all extracted entities.
- `GET /relationships` - List relationships between entities.
- `GET /graph` - Return the complete graph.

## Plugin Architecture

This example demonstrates how to create a custom plugin:

```python
from core.plugins import AgentPlugin, GraphPlugin

class DocumentAnalyzerPlugin(AgentPlugin, GraphPlugin):
    name = "document-analyzer"
    
    def register_entity_types(self):
        return [
            {"type": "person", "display": "Person"},
            {"type": "organization", "display": "Organization"},
            {"type": "document", "display": "Document"},
        ]
```

## Output Example

```json
{
  "entities": [
    {"type": "person", "name": "John Doe", "mentions": 5},
    {"type": "organization", "name": "ACME Corp", "mentions": 3}
  ],
  "relationships": [
    {"source": "John Doe", "relation": "works_at", "target": "ACME Corp"}
  ]
}
```
