"""
Document Analyzer - Multi-agent document analysis example.

Demonstrates:
- Entity extraction from documents
- Relationship mapping
- Knowledge graph construction
- Plugin architecture usage
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import uvicorn


# ============================================================================
# Models
# ============================================================================


class Entity(BaseModel):
    """Extracted entity."""

    id: str
    type: str
    name: str
    mentions: int = 1
    metadata: dict = {}


class Relationship(BaseModel):
    """Relationship between entities."""

    source_id: str
    relation: str
    target_id: str
    confidence: float = 1.0


class AnalysisResult(BaseModel):
    """Document analysis result."""

    document_id: str
    entities: list[Entity]
    relationships: list[Relationship]
    summary: str


# ============================================================================
# Document Analyzer
# ============================================================================


class DocumentAnalyzer:
    """Simple document analyzer with entity extraction."""

    def __init__(self):
        self.entities: dict[str, Entity] = {}
        self.relationships: list[Relationship] = []

    async def analyze(self, content: str, filename: str) -> AnalysisResult:
        """Analyze document content and extract entities."""
        import re
        import uuid

        doc_id = str(uuid.uuid4())[:8]

        # Simple entity extraction (demo - use NER in production)
        # Extract capitalized words as potential entities
        words = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", content)
        word_counts = {}
        for word in words:
            if len(word) > 2:
                word_counts[word] = word_counts.get(word, 0) + 1

        # Create entities
        entities = []
        for name, count in word_counts.items():
            entity = Entity(
                id=f"e_{len(entities)}",
                type="unknown",  # Would use NER to classify
                name=name,
                mentions=count,
            )
            entities.append(entity)
            self.entities[entity.id] = entity

        # Generate simple relationships (demo)
        relationships = []
        if len(entities) >= 2:
            rel = Relationship(
                source_id=entities[0].id,
                relation="mentioned_with",
                target_id=entities[1].id,
            )
            relationships.append(rel)
            self.relationships.append(rel)

        return AnalysisResult(
            document_id=doc_id,
            entities=entities[:10],  # Limit for demo
            relationships=relationships,
            summary=f"Analyzed {filename}: found {len(entities)} entities",
        )

    def get_all_entities(self) -> list[Entity]:
        """Get all extracted entities."""
        return list(self.entities.values())

    def get_all_relationships(self) -> list[Relationship]:
        """Get all relationships."""
        return self.relationships


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Document Analyzer",
    description="Multi-agent document analysis example",
    version="1.0.0",
)

analyzer = DocumentAnalyzer()


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "entities_count": len(analyzer.entities),
        "relationships_count": len(analyzer.relationships),
    }


@app.post("/analyze", response_model=AnalysisResult)
async def analyze_document(file: UploadFile = File(...)):
    """Analyze uploaded document."""
    try:
        content = (await file.read()).decode("utf-8", errors="ignore")
        result = await analyzer.analyze(content, file.filename or "unknown")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/entities", response_model=list[Entity])
async def get_entities():
    """Get all extracted entities."""
    return analyzer.get_all_entities()


@app.get("/relationships", response_model=list[Relationship])
async def get_relationships():
    """Get all relationships."""
    return analyzer.get_all_relationships()


@app.get("/graph")
async def get_graph():
    """Get knowledge graph as nodes and edges."""
    nodes = [
        {"id": e.id, "label": e.name, "type": e.type}
        for e in analyzer.entities.values()
    ]
    edges = [
        {"source": r.source_id, "target": r.target_id, "label": r.relation}
        for r in analyzer.relationships
    ]
    return {"nodes": nodes, "edges": edges}


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
