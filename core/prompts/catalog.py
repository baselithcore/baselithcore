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

import os
import re
from collections.abc import Mapping
from pathlib import Path

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Directory holding the packaged catalog files (``<name>.md``).
CATALOG_DIR = Path(__file__).parent / "catalog"

#: Env prefix for per-prompt A/B weights: ``BASELITH_PROMPT_VARIANTS_<NAME>``
#: (name uppercased, non-alphanumerics as underscores) = ``"1:50,2:50"``
#: (version:weight pairs). When set, the prompt is resolved through
#: ``select_variant`` with a stable subject instead of the label path.
VARIANTS_ENV_PREFIX = "BASELITH_PROMPT_VARIANTS_"


def _variant_weights(name: str) -> dict[str, int] | None:
    """Parse the prompt's A/B weights from env, or None when unset/invalid."""
    env_name = VARIANTS_ENV_PREFIX + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return None
    weights: dict[str, int] = {}
    try:
        for pair in raw.split(","):
            version, weight = pair.split(":", 1)
            weights[version.strip()] = int(weight.strip())
    except ValueError:
        logger.warning(
            "prompt_variants_env_malformed",
            extra={"env": env_name, "value": raw},
        )
        return None
    return weights or None


def resolve_catalog_prompt(
    name: str,
    variables: Mapping[str, object] | None = None,
    *,
    catalog_file: Path | None = None,
    fallback_template: str | None = None,
    label: str = "production",
    subject: str | None = None,
) -> str:
    """Render catalog prompt ``name`` with ``variables``.

    Args:
        name: Prompt name in the registry (and ``<name>.md`` in the catalog).
        variables: Values for the template's ``{{ var }}`` placeholders.
        catalog_file: Override the packaged file location (tests/plugins).
        fallback_template: Embedded template rendered when the registry or
            the catalog file is unavailable; without one, failures propagate.
        label: Preferred label, resolved before falling back to latest.
        subject: Stable A/B bucketing subject. Only consulted when the
            prompt has weights configured (``BASELITH_PROMPT_VARIANTS_<NAME>``);
            defaults to the ambient tenant, so an experiment can be switched
            on per prompt via env alone, with per-tenant stable variants.

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

        weights = _variant_weights(name)
        if weights:
            if subject is None:
                from core.context import get_tenant_or_default

                subject = get_tenant_or_default()
            try:
                variant = registry.select_variant(name, subject, weights)
                rendered = registry.render(
                    name, dict(variables or {}), version=variant.version
                )
                return rendered.text
            except PromptNotFoundError:
                logger.warning(
                    "prompt_variant_unresolved_falling_back_label",
                    extra={"prompt": name, "weights": weights},
                )

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
