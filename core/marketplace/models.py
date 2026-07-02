"""
Marketplace Data Models.

Defines the structures for plugin metadata, categories, and registry content
used by the marketplace discovery engine.
Standardized to be coherent with the Baselith Marketplace Plugin.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PluginCategory(str, Enum):
    """Categories for marketplace plugins."""

    ALL = "all"
    AGENT = "agent"
    TOOL = "tool"
    SECURITY = "security"
    UTILITY = "utility"
    ANALYSIS = "analysis"
    INTEGRATION = "integration"
    WORKFLOW = "workflow"
    UI = "ui"
    OTHER = "other"


class PluginStatus(str, Enum):
    """Status of a plugin in the marketplace."""

    AVAILABLE = "available"
    INSTALLED = "installed"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    STABLE = "stable"  # Compatibility with previous core impl
    BETA = "beta"


class MarketplacePlugin(BaseModel):
    """
    Metadata for plugins in the marketplace.
    Uses Pydantic for easy serialization/deserialization from the remote registry.
    """

    id: str = Field(..., description="Unique ID for the plugin (e.g., 'org.plugin')")
    name: str = Field(..., description="Display name of the plugin")
    version: str = Field(..., description="Semantic version")
    description: str | None = Field(None)
    author: str | None = Field("unknown")

    category: PluginCategory = Field(default=PluginCategory.OTHER)
    status: PluginStatus = Field(default=PluginStatus.AVAILABLE)

    # Stats
    downloads: int = Field(default=0)
    stars: int = Field(default=0)
    rating: float = Field(default=0.0)
    rating_count: int = Field(default=0)

    # URLs
    git_url: str | None = Field(
        None, alias="repository", description="Repository URL for installation"
    )
    homepage: str | None = Field(None)
    license: str = Field(default="AGPL-3.0")
    tags: list[str] = Field(default_factory=list)

    # Dependencies
    dependencies: list[str] = Field(default_factory=list)
    python_requires: str = Field(
        default=">=3.10", description="Python version requirements"
    )
    plugin_dependencies: dict[str, str] = Field(default_factory=dict)

    # Compatibility
    min_framework_version: str = Field(default="2.0.0")

    @field_validator("plugin_dependencies", mode="before")
    @classmethod
    def _coerce_empty_list_to_dict(cls, v: Any) -> Any:
        """Server publishes ``plugin_dependencies: []`` when there are no deps;
        accept both list and dict shapes."""
        if isinstance(v, list):
            if not v:
                return {}
            # ["name>=1.0", ...] or [{"name": "...", "version": "..."}]
            coerced: dict[str, str] = {}
            for item in v:
                if isinstance(item, dict) and "name" in item:
                    coerced[item["name"]] = item.get("version", "*")
                elif isinstance(item, str):
                    if ">=" in item:
                        name, _, version = item.partition(">=")
                        coerced[name.strip()] = ">=" + version.strip()
                    else:
                        coerced[item.strip()] = "*"
            return coerced
        return v

    class Config:
        populate_by_name = True


class PluginReview(BaseModel):
    """User review for a marketplace plugin."""

    id: str | None = None
    plugin_id: str
    user_id: str
    rating: int = Field(..., ge=1, le=5)
    title: str = ""
    content: str = ""
    created_at: datetime | None = None


class RegistryData(BaseModel):
    """Structure of the marketplace registry JSON."""

    version: str
    last_updated: str
    plugins: list[MarketplacePlugin]
    categories: list[dict[str, str]] | None = None
