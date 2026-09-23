"""Strict schema for plugin manifests.

``manifest.yaml`` is the plugin contract: it decides which code runs, what that
code may reach, and which core versions accept it. It used to be read with a
pile of ``data.get(...)`` calls, which meant a misspelled key
(``min_core_verison``) silently vanished — the plugin loaded with *no* version
floor and nobody was told. :class:`PluginManifestModel` closes that: every key
the framework understands is declared here, and anything else is a hard,
named error.

A closed key set needs an open door for data the *core* does not interpret:
a plugin ecosystem cannot make the framework learn every consumer's schema,
and dropping such keys silently is the exact failure this model exists to
stop. That door is the ``x-`` prefix (:data:`VENDOR_EXTENSION_PREFIX`, the
convention from HTTP headers and OpenAPI): ``x-anything`` is accepted
verbatim, collected into :attr:`PluginManifestModel.extensions`, surfaced as
``PluginMetadata.extensions``, and — because the V5 integrity surface hashes
the whole canonicalised manifest minus the three self-referential digest keys
— covered by the plugin signature like every other key. No core key starts
with ``x-``, so the two namespaces cannot collide and a misspelled core key
(which never grows a prefix by accident) is still a hard error.

The model lives in its own module (rather than beside :class:`PluginMetadata`
in ``_metadata.py``) to respect the 500-line cap; ``_metadata`` re-exports it,
so ``from core.plugins._metadata import PluginManifestModel`` also works.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    field_validator,
)

#: Marks a manifest key the core deliberately does not interpret. Borrowed
#: from HTTP headers and OpenAPI, where it means exactly this: "mine, not
#: yours". No field of :class:`PluginManifestModel` starts with it, and none
#: ever may — that disjointness is what keeps typo detection intact.
VENDOR_EXTENSION_PREFIX = "x-"

#: What may follow the prefix: a leading alphanumeric, then the characters a
#: reverse-DNS or path-ish vendor namespace needs (``x-acme.io/feature-2``).
#: Spaces, punctuation and an empty name are refused — those are accidents,
#: not namespaces.
_EXTENSION_KEY_RE = re.compile(r"^x-[A-Za-z0-9][A-Za-z0-9._/-]*$")

#: Non-optional fields whose YAML value may legitimately be blank. ``foo:`` with
#: nothing after it parses as ``None``, which is how a human writes "I left this
#: empty" — it is not an unknown key and must not be a validation failure. Each
#: falls back to the field's own default.
_NULLABLE_DEFAULT_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "id",
    "homepage",
    "repository",
    "license",
    "icon",
    "category",
    "readiness",
    "subcomponent_of",
    "tenancy",
    "system",
)

#: List-typed fields a manifest may abbreviate to a single scalar
#: (``tags: example`` rather than ``tags: [example]``).
_SCALAR_TO_LIST_FIELDS = (
    "dependencies",
    "python_dependencies",
    "required_resources",
    "optional_resources",
    "tags",
    "screenshots",
    "environment_variables",
    "llm_scopes",
)


class ManifestValidationError(ValueError):
    """Raised when a manifest does not satisfy :class:`PluginManifestModel`.

    A ``ValueError`` subclass so the existing ``except Exception`` handlers in
    the loader and the resource analyzer keep treating a bad manifest as a
    skipped plugin rather than a crashed process.
    """


class PluginManifestModel(BaseModel):
    """Every key a BaselithCore plugin manifest may declare.

    ``extra="forbid"``: an unknown key is a typo or a manifest written against
    a different framework, and either way silently ignoring it hides a
    misconfiguration. :func:`validate_manifest_data` turns that into a message
    naming the offending key and its nearest known neighbour.

    Field types are deliberately permissive (``Any`` for the nested
    ``permissions`` block, ``list[Any]`` for ``llm_scopes``): the dedicated
    normalizers in :mod:`core.plugins.permissions` and
    :class:`~core.plugins._metadata.PluginMetadata` already coerce those
    shapes and are tolerant of malformed rows by design. This model polices
    the *key set*, not the interior of every value.

    Keys prefixed ``x-`` are the one exception to ``extra="forbid"``: they are
    not fields, they are split out before validation by
    :func:`validate_manifest_data` and kept in :attr:`extensions`. Constructing
    the model directly still forbids them — the split is the only door, so the
    manifest is validated the same way no matter who parses it.
    """

    model_config = ConfigDict(
        extra="forbid",
        # YAML turns an unquoted ``version: 1.0`` into a float; the framework
        # wants the string. Coerce rather than reject — the manifest is authored
        # by hand and this is the single most common accident.
        coerce_numbers_to_str=True,
    )

    # --- Identity -----------------------------------------------------------
    name: str = ""
    #: Human-readable name for consoles and the catalog. Presentation only —
    #: ``name`` remains the identifier; empty ⇒ a title derived from ``name``.
    display_name: str = ""
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    #: Marketplace identifier; defaults to the directory name when absent.
    id: str = ""
    #: ``module:Class`` pointing at the plugin class, relative to the plugin
    #: package. Absent ⇒ the loader falls back to scanning the module.
    entry_point: str | None = None
    #: Legacy spelling accepted for plugins created before ``entry_point`` was
    #: the canonical manifest key.
    entrypoint: str | None = None

    # --- Dependencies -------------------------------------------------------
    dependencies: list[str] | None = None
    python_dependencies: list[str] | None = None
    plugin_dependencies: dict[str, str] | None = None
    required_resources: list[str] | None = None
    optional_resources: list[str] | None = None

    # --- Compatibility ------------------------------------------------------
    min_core_version: str | None = None
    max_core_version: str | None = None

    # --- Presentation -------------------------------------------------------
    homepage: str = ""
    repository: str = ""
    #: Legacy spelling of ``repository`` accepted by the marketplace validator.
    git_url: str | None = None
    license: str = ""
    tags: list[str] | None = None
    icon: str = ""
    screenshots: list[str] | None = None
    category: str = "Generic"
    readiness: str = "stable"
    subcomponent_of: str = ""

    # --- Runtime posture ----------------------------------------------------
    environment_variables: list[Any] | None = None
    frontend: Any = None
    health_endpoint: str | None = None
    system: bool = False
    tenancy: str = "shared"
    llm_scopes: list[Any] | None = None
    permissions: Any = None

    # --- Supply chain -------------------------------------------------------
    integrity_sha256: str | None = None
    signature_ed25519: str | None = None
    #: Version of the hashed-surface definition the digest was computed under.
    #: Read by :mod:`core.plugins.integrity`; declared here so a manifest
    #: carrying it validates.
    hash_surface_version: int | str | None = None

    #: Vendor extensions, keyed by the manifest key exactly as written
    #: (``"x-control"``). A private attribute rather than a field so
    #: ``extensions:`` never becomes a second, un-prefixed way in — see
    #: :func:`validate_manifest_data`, which is what populates it.
    _extensions: dict[str, Any] = PrivateAttr(default_factory=dict)

    @property
    def extensions(self) -> dict[str, Any]:
        """Vendor-extension keys carried by this manifest.

        Returns:
            The ``x-``-prefixed keys and their values, keyed as written in the
            manifest. Empty for a manifest that declares none.
        """
        return self._extensions

    @field_validator(*_NULLABLE_DEFAULT_FIELDS, mode="before")
    @classmethod
    def _blank_means_default(cls, value: Any, info: ValidationInfo) -> Any:
        """Treat an explicitly empty YAML scalar as "field not set".

        ``description:`` with nothing after it is ``None`` once YAML is parsed.
        This model polices the *key set*, not a house style for blank lines, so
        a null here falls back to the field's own default (``"0.1.0"`` for
        ``version``, ``"Generic"`` for ``category``, ...) instead of failing.
        """
        if value is not None:
            return value
        field = cls.model_fields.get(info.field_name or "")
        return field.default if field is not None else ""

    @field_validator(*_SCALAR_TO_LIST_FIELDS, mode="before")
    @classmethod
    def _scalar_becomes_one_item(cls, value: Any) -> Any:
        """Accept ``tags: example`` as ``tags: [example]``.

        A one-element list written without brackets is the single most common
        hand-authored manifest shorthand; rejecting it would fail a manifest
        whose key set is entirely correct.
        """
        if isinstance(value, str | int | float | bool):
            return [value]
        return value


def describe_manifest_failure(manifest_path: Path) -> str:
    """Re-read a manifest that failed elsewhere and report *why*, log-safely.

    Discovery swallows a manifest parse error (it must keep scanning the rest
    of the tree), so the exact message — which names the offending key and its
    nearest known neighbour — is recovered here for the caller's refusal log.

    Args:
        manifest_path: The manifest that did not yield metadata.

    Returns:
        A human-readable reason, escaped for logging (manifest content is
        author-supplied and therefore untrusted).
    """
    from core.utils.logsafe import sanitize_log_value

    # The default truncation limit cuts the message mid-hint, losing the
    # "did you mean 'min_core_version'?" half that makes it actionable. The
    # content is still escaped — only the length budget is raised.
    limit = 1024

    try:
        load_manifest_model(manifest_path)
    except ManifestValidationError as exc:
        return sanitize_log_value(str(exc), max_length=limit)
    except Exception as exc:  # unreadable file, unexpected shape
        return sanitize_log_value(f"{manifest_path}: {exc}", max_length=limit)
    return sanitize_log_value(
        f"{manifest_path}: parsed, but the plugin could not be analysed",
        max_length=limit,
    )


def known_manifest_keys() -> frozenset[str]:
    """The complete set of keys :class:`PluginManifestModel` accepts.

    Returns:
        Every declared field name, for callers that want to report or document
        the manifest surface (the CLI validator, the marketplace). Vendor
        extensions are deliberately absent: the ``x-`` namespace is open by
        construction, so it is a *rule*, not a set of keys.
    """
    return frozenset(PluginManifestModel.model_fields)


def is_extension_key(key: object) -> bool:
    """Whether ``key`` claims the vendor-extension namespace.

    Args:
        key: A manifest key. Non-strings (YAML permits them) are never
            extensions.

    Returns:
        ``True`` for a string starting with :data:`VENDOR_EXTENSION_PREFIX`.
        Says nothing about whether the rest of the name is well-formed —
        :func:`validate_manifest_data` decides that.
    """
    return isinstance(key, str) and key.startswith(VENDOR_EXTENSION_PREFIX)


def split_extension_keys(
    data: Mapping[Any, Any],
) -> tuple[dict[Any, Any], dict[str, Any]]:
    """Partition a manifest mapping into core keys and vendor extensions.

    Args:
        data: The parsed manifest mapping.

    Returns:
        ``(core, extensions)``: the keys the framework interprets, and the
        ``x-``-prefixed ones it carries but never reads.
    """
    core: dict[Any, Any] = {}
    extensions: dict[str, Any] = {}
    for key, value in data.items():
        if is_extension_key(key):
            extensions[str(key)] = value
        else:
            core[key] = value
    return core, extensions


def _suggested_extension_key(key: str) -> str | None:
    """The legal ``x-`` spelling of ``key``, when one exists.

    ``control`` ⇒ ``x-control``; a near-miss prefix (``X-Control``, ``x_control``)
    is normalized rather than suggested verbatim, because that author already
    knew about the namespace and only mistyped the prefix.
    """
    lowered = key.lower()
    if lowered.startswith(("x-", "x_")):
        candidate = VENDOR_EXTENSION_PREFIX + lowered[2:]
    else:
        candidate = VENDOR_EXTENSION_PREFIX + key
    return candidate if _EXTENSION_KEY_RE.match(candidate) else None


def _is_near_miss_prefix(key: str) -> bool:
    """Whether ``key`` is an extension key with a mistyped prefix."""
    return key.lower().startswith(("x-", "x_"))


def _describe_unknown(keys: list[Any]) -> str:
    """Render unknown keys with a hint: nearest known key, or the ``x-`` form.

    A key close to a declared one is almost certainly a typo, so that hint
    wins. Everything else is more likely vendor data in the wrong namespace,
    which is fixable — so the message shows the spelling that would be legal.
    """
    known = sorted(known_manifest_keys())
    parts: list[str] = []
    for key in keys:
        text = str(key)
        close = difflib.get_close_matches(text, known, n=1, cutoff=0.6)
        suggestion = _suggested_extension_key(text)
        if close and not _is_near_miss_prefix(text):
            hint = f" (did you mean '{close[0]}'?)"
        elif suggestion:
            hint = f" (vendor data? declare it as '{suggestion}')"
        else:
            hint = ""
        parts.append(f"'{text}'{hint}")
    return ", ".join(parts)


#: Appended to every unknown-key refusal: the rule a plugin author needs in
#: order to make their key legal, stated where they will actually read it.
_EXTENSION_RULE = (
    "Keys the core does not interpret are legal only under the "
    f"'{VENDOR_EXTENSION_PREFIX}' prefix (e.g. 'x-control'): those are kept "
    "verbatim, reachable as PluginMetadata.extensions, and covered by the "
    "plugin integrity digest. Anything else is a typo — fix the spelling or "
    "remove the key; the loader ignores nothing."
)


def validate_manifest_data(
    data: Any,
    *,
    source: Path | str | None = None,
) -> PluginManifestModel:
    """Validate a parsed manifest mapping against the strict schema.

    Args:
        data: The mapping produced by ``yaml.safe_load`` / ``json.load``.
        source: Manifest path, used only to prefix the error message.

    Returns:
        The validated :class:`PluginManifestModel`, with any ``x-`` keys
        collected into :attr:`PluginManifestModel.extensions`.

    Raises:
        ManifestValidationError: The payload is not a mapping, declares an
            unknown key, carries a malformed ``x-`` key, or fails a field's
            type constraint. The message names every offending key and, for
            unknown keys, either the closest known one or the ``x-`` spelling
            that would make it legal.
    """
    where = f"{source}: " if source else ""

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ManifestValidationError(
            f"{where}manifest must be a mapping, got {type(data).__name__}"
        )

    core_data, extensions = split_extension_keys(data)

    malformed = [key for key in extensions if not _EXTENSION_KEY_RE.match(key)]
    if malformed:
        listed = ", ".join(f"'{key}'" for key in malformed)
        raise ManifestValidationError(
            f"{where}malformed vendor extension key(s): {listed}. After the "
            f"'{VENDOR_EXTENSION_PREFIX}' prefix use a letter or digit, then "
            "letters, digits, '.', '_', '-' or '/' (e.g. 'x-control')."
        )

    unknown = [key for key in core_data if key not in PluginManifestModel.model_fields]
    if unknown:
        raise ManifestValidationError(
            f"{where}unknown manifest key(s): {_describe_unknown(unknown)}. "
            f"{_EXTENSION_RULE}"
        )

    try:
        model = PluginManifestModel.model_validate(core_data)
    except ValidationError as exc:
        problems = "; ".join(
            f"'{'.'.join(str(p) for p in err['loc']) or '<root>'}': {err['msg']}"
            for err in exc.errors()
        )
        raise ManifestValidationError(f"{where}invalid manifest: {problems}") from exc

    model._extensions = extensions
    return model


def load_manifest_model(path: Path) -> PluginManifestModel:
    """Read and validate a manifest file.

    Args:
        path: Path to a ``.yaml``, ``.yml`` or ``.json`` manifest.

    Returns:
        The validated :class:`PluginManifestModel`.

    Raises:
        ManifestValidationError: The file cannot be parsed, or fails the schema.
    """
    try:
        if path.suffix in (".yaml", ".yml"):
            import yaml

            with open(path, encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        else:
            import json

            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
    except ManifestValidationError:
        raise
    except Exception as exc:  # malformed YAML/JSON, unreadable file
        raise ManifestValidationError(
            f"{path}: could not parse manifest: {exc}"
        ) from exc

    return validate_manifest_data(data, source=path)


__all__ = [
    "VENDOR_EXTENSION_PREFIX",
    "ManifestValidationError",
    "describe_manifest_failure",
    "PluginManifestModel",
    "is_extension_key",
    "known_manifest_keys",
    "load_manifest_model",
    "split_extension_keys",
    "validate_manifest_data",
]
