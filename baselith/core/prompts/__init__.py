"""
Prompt registry — versioned prompt templates with labels, A/B selection, and
file-based loading.

Register prompts in code or load them from a directory of Markdown files, then
resolve by version or label and render with variables. The
:class:`~baselith.core.prompts.types.RenderedPrompt` carries name+version so call sites
can attach prompt provenance to trace spans and evaluations.
"""

from baselith.core.prompts.catalog import resolve_catalog_prompt
from baselith.core.prompts.loader import (
    PromptLoadError,
    load_prompts_from_dir,
    parse_prompt_file,
)
from baselith.core.prompts.registry import (
    InMemoryPromptStore,
    PromptRegistry,
    PromptStore,
    get_prompt_registry,
)
from baselith.core.prompts.rendering import find_placeholders, render_template
from baselith.core.prompts.types import (
    PromptError,
    PromptNotFoundError,
    PromptRenderError,
    PromptVersion,
    RenderedPrompt,
    compute_checksum,
)

__all__ = [
    "InMemoryPromptStore",
    "PromptError",
    "PromptLoadError",
    "PromptNotFoundError",
    "PromptRegistry",
    "PromptRenderError",
    "PromptStore",
    "PromptVersion",
    "RenderedPrompt",
    "compute_checksum",
    "find_placeholders",
    "get_prompt_registry",
    "load_prompts_from_dir",
    "parse_prompt_file",
    "render_template",
    "resolve_catalog_prompt",
]
