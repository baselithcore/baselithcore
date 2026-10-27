"""
Knowledge API configuration.

Controls the HTTP surface for managing and querying the knowledge base
(ingest, delete, search): enablement, which collections it exposes, and the
size bounds on what a client may send. Opt-in and default-off, so a deployment
that does not use it exposes nothing new.
"""

from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from baselith.core.config._aliased import AliasedSettings
from baselith.core.config._collections import csv_list
from baselith.core.config.overrides import overridable


class KnowledgeApiConfig(AliasedSettings):
    """Configuration for the knowledge base HTTP API."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # Master switch: mounts /knowledge (ingest, upload, delete, search).
    enabled: bool = Field(default=False, alias="KNOWLEDGE_API_ENABLED")

    # Collections a client may name besides the configured default one. A
    # client that could name any collection could write into the ones that
    # hold agent memory, or create new ones at will; the default collection is
    # always available, everything else is opt-in here.
    collections: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), alias="KNOWLEDGE_COLLECTIONS"
    )

    # Largest upload accepted, in bytes. Checked while the body streams in, not
    # after it has been buffered.
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024, alias="KNOWLEDGE_MAX_UPLOAD_BYTES", ge=1024
    )
    # Largest document text accepted, in characters, whether it was posted as
    # text or extracted from a file (a small PDF can hold a lot of text).
    max_text_chars: int = Field(
        default=2_000_000, alias="KNOWLEDGE_MAX_TEXT_CHARS", ge=1000
    )

    @field_validator("collections", mode="before")
    @classmethod
    def _parse_collections(cls, value: Any) -> Any:
        """Accept ``a,b``, a blank value, or a JSON array (see ``csv_list``)."""
        return csv_list(value)


_knowledge_config: KnowledgeApiConfig | None = None


@overridable(KnowledgeApiConfig)
def get_knowledge_config() -> KnowledgeApiConfig:
    """Get or create the global knowledge API configuration instance."""
    global _knowledge_config
    if _knowledge_config is None:
        _knowledge_config = KnowledgeApiConfig()
    return _knowledge_config
