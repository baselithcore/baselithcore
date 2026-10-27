"""YAML serialization for workflow definitions.

A definition is a plain mapping, so YAML (and JSON, which YAML subsumes) is a
faithful, reviewable on-disk form: workflows can live in version control, be
diffed, and be loaded by ``baselith workflow`` or the catalog directory.

Loading is hardened because definitions may arrive over the API: the text is
size-capped, parsed with the safe loader, and rejected if it uses anchors or
aliases (which let a few hundred bytes expand into an exponentially large
structure once the definition is walked or re-serialized).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .builder import WorkflowDefinition

__all__ = [
    "MAX_DEFINITION_BYTES",
    "DefinitionError",
    "definition_from_mapping",
    "dump_yaml",
    "load_definition",
    "load_definition_file",
    "load_mapping",
    "write_definition_file",
]

#: Largest definition text accepted (2 MiB).
MAX_DEFINITION_BYTES = 2 * 1024 * 1024


class DefinitionError(ValueError):
    """A definition could not be parsed into a workflow."""


def dump_yaml(workflow: WorkflowDefinition) -> str:
    """Render ``workflow`` as YAML.

    Raises:
        DefinitionError: If a node's config holds something YAML cannot
            represent (a callable passed to ``config['fn']``, for example).
    """
    try:
        dumped: str = yaml.safe_dump(
            workflow.to_dict(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        return dumped
    except yaml.YAMLError as exc:
        raise DefinitionError(
            "workflow holds values that cannot be exported (callables or "
            f"objects in a node config): {exc}"
        ) from exc


def _reject_aliases(text: str) -> None:
    try:
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            if isinstance(token, yaml.AnchorToken | yaml.AliasToken):
                raise DefinitionError("YAML anchors and aliases are not allowed")
    except yaml.YAMLError as exc:
        raise DefinitionError(f"invalid YAML: {exc}") from exc


def load_mapping(text: str) -> dict[str, Any]:
    """Parse YAML or JSON text into the raw definition mapping.

    Applies the hardening described in the module docstring (size cap, safe
    loader, no anchors or aliases) without building a definition, for callers
    that need to look at the mapping first.

    Raises:
        DefinitionError: Oversized, malformed, aliased, or not a mapping.
    """
    if len(text.encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise DefinitionError(f"definition larger than {MAX_DEFINITION_BYTES} bytes")
    _reject_aliases(text)
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DefinitionError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise DefinitionError("a workflow definition must be a mapping")
    return data


def definition_from_mapping(data: dict[str, Any]) -> WorkflowDefinition:
    """Build a definition from a mapping, reporting problems uniformly.

    The graph itself is not validated here; call
    :meth:`WorkflowDefinition.validate` for that.

    Raises:
        DefinitionError: Unknown node type, missing ids, bad cron, wrong shapes.
    """
    try:
        return WorkflowDefinition.from_dict(data)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DefinitionError(f"invalid workflow definition: {exc}") from exc


def load_definition(text: str) -> WorkflowDefinition:
    """Parse YAML or JSON text into a definition (see :func:`load_mapping`)."""
    return definition_from_mapping(load_mapping(text))


def load_definition_file(path: str | Path) -> WorkflowDefinition:
    """Read and parse a ``.yaml`` / ``.yml`` / ``.json`` definition file."""
    file = Path(path)
    if file.stat().st_size > MAX_DEFINITION_BYTES:
        raise DefinitionError(f"{file.name}: larger than {MAX_DEFINITION_BYTES} bytes")
    return load_definition(file.read_text(encoding="utf-8"))


def write_definition_file(workflow: WorkflowDefinition, path: str | Path) -> Path:
    """Write ``workflow`` as YAML to ``path`` atomically and return the path."""
    target = Path(path)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(dump_yaml(workflow), encoding="utf-8")
    tmp.replace(target)
    return target
