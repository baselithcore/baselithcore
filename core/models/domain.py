"""
Core domain models for the application.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Document(BaseModel):
    """
    Represents a document in the system.
    """

    content: str
    id: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    vector: Optional[List[float]] = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def ensure_id_from_metadata(self) -> "Document":
        """Ensure ID is set if possible from metadata."""
        if not self.id and self.metadata and self.metadata.get("id"):
            self.id = str(self.metadata["id"])
        return self


class SearchResult(BaseModel):
    """
    Represents a search result from the vector store.
    """

    document: Document
    score: float

    model_config = ConfigDict(extra="ignore")
