"""
Domain types for the prompt registry.

A :class:`PromptVersion` is an immutable, content-addressed record of one prompt
template at one version, optionally carrying labels (``production``, ``staging``,
…) used to resolve "the current production prompt" without pinning a version.
:class:`RenderedPrompt` carries the resolved name+version alongside the final
text so a call site can attach it to a trace span or evaluation record.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, ConfigDict, Field


class PromptError(Exception):
    """Base class for prompt registry errors."""


class PromptNotFoundError(PromptError):
    """No prompt matched the requested name/version/label."""


class PromptRenderError(PromptError):
    """Rendering failed (e.g. a required variable was missing)."""


def compute_checksum(template: str) -> str:
    """Stable content hash of a template body (first 16 hex chars of sha256)."""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]


class PromptVersion(BaseModel):
    """One immutable version of a named prompt template."""

    name: str
    version: str = "1"
    template: str
    description: Optional[str] = None
    # Resolution labels. A label points to exactly one version of a name at a
    # time (enforced by the registry on promote).
    labels: Set[str] = Field(default_factory=set)
    # Declared variables; when non-empty, rendering validates that all are
    # supplied and rejects unknown ones.
    variables: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    checksum: str = ""

    model_config = ConfigDict(extra="forbid")

    def model_post_init(self, _context: Any) -> None:
        if not self.checksum:
            # Pydantic models are not frozen here; set the derived checksum once.
            object.__setattr__(self, "checksum", compute_checksum(self.template))

    def key(self) -> str:
        """Unique ``name@version`` identifier."""
        return f"{self.name}@{self.version}"


class RenderedPrompt(BaseModel):
    """The result of rendering a prompt — text plus provenance for tracing."""

    text: str
    name: str
    version: str
    checksum: str
    variables: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    def span_attributes(self) -> Dict[str, str]:
        """OpenTelemetry-friendly attributes linking output to its prompt.

        Attach to the LLM call span so traces and evaluations can be grouped by
        prompt name+version (the foundation of online prompt evaluation/A-B).
        """
        return {
            "prompt.name": self.name,
            "prompt.version": self.version,
            "prompt.checksum": self.checksum,
        }
