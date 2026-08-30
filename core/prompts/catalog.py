"""Packaged prompt catalog — registry-served prompts with embedded fallbacks.

Generalizes the pattern pioneered by ``core.chat.prompt`` (conversation
system prompt) so every hot-path prompt can be served from the versioned
:class:`~core.prompts.registry.PromptRegistry` instead of a hardcoded string:

* the canonical template ships as a Markdown file (YAML front matter + body)
  under ``core/prompts/catalog/`` and is seeded into the global registry on
  first use;
* a deployment catalog loaded via ``BASELITH_PROMPTS_DIR`` registers first,
  so its versions/labels win over the packaged defaults;
* resolution is ``production`` label > latest registered version > the
  caller's embedded fallback template (registry unavailable / file missing);
* every registry render emits the ``prompt.render`` provenance span, so LLM
  spans are attributable to a prompt name/version/checksum.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Directory holding the packaged catalog files (``<name>.md``).
CATALOG_DIR = Path(__file__).parent / "catalog"


def resolve_catalog_prompt(
    name: str,
    variables: Mapping[str, object] | None = None,
    *,
    catalog_file: Path | None = None,
    fallback_template: str | None = None,
    label: str = "production",
) -> str:
    """Render catalog prompt ``name`` with ``variables``.

    Args:
        name: Prompt name in the registry (and ``<name>.md`` in the catalog).
        variables: Values for the template's ``{{ var }}`` placeholders.
        catalog_file: Override the packaged file location (tests/plugins).
        fallback_template: Embedded template rendered when the registry or
            the catalog file is unavailable; without one, failures propagate.
        label: Preferred label, resolved before falling back to latest.

    Returns:
        The rendered prompt text.
    """
    path = catalog_file if catalog_file is not None else CATALOG_DIR / f"{name}.md"
    try:
        from core.prompts.loader import parse_prompt_file
        from core.prompts.registry import get_prompt_registry
        from core.prompts.types import PromptNotFoundError

        registry = get_prompt_registry()
        # Seed from the packaged file only when nothing is registered yet —
        # a deployment catalog (BASELITH_PROMPTS_DIR) or programmatic
        # registration wins over the packaged default.
        if not registry.list_versions(name):
            registry.store.put(parse_prompt_file(path))
        try:
            rendered = registry.render(name, dict(variables or {}), label=label)
        except PromptNotFoundError:
            rendered = registry.render(name, dict(variables or {}))
        return rendered.text
    except Exception as exc:
        if fallback_template is None:
            raise
        logger.warning(
            "prompt_catalog_fallback",
            extra={"prompt": name, "error": str(exc)},
        )
        from core.prompts.rendering import render_template

        return render_template(fallback_template, dict(variables or {}), strict=False)


__all__ = ["CATALOG_DIR", "resolve_catalog_prompt"]
