"""Tests for vendor extensions in plugin manifests (the ``x-`` namespace).

``PluginManifestModel`` is fail-closed (``extra="forbid"``) so a misspelled
core key is a named error rather than a silently ignored line. That left no
legal place for data the *plugin* understands and the core does not, which
stopped downstream manifests loading at all.

These tests pin the escape hatch and its boundaries: an ``x-``-prefixed key is
carried through verbatim and stays reachable, a key without the prefix is
still refused (with the did-you-mean hint *and* the rule that would make it
legal), and the extension data is inside the V5 integrity digest rather than a
hole in it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.plugins._metadata import PluginMetadata
from core.plugins.integrity import compute_plugin_hash
from core.plugins.manifest_model import (
    VENDOR_EXTENSION_PREFIX,
    ManifestValidationError,
    PluginManifestModel,
    describe_manifest_failure,
    is_extension_key,
    known_manifest_keys,
    load_manifest_model,
    validate_manifest_data,
)

REPO_ROOT = Path(__file__).resolve().parents[4]

# The manifest shapes that blocked the downstream deployment: a key the owning
# plugin reads and the core has no opinion about.
DOWNSTREAM_KEYS = ("control", "news_feeds", "runtime_prerequisites")


class TestTypoProtectionSurvives:
    """The reason ``extra="forbid"`` exists must not be weakened."""

    def test_misspelled_core_key_still_refused_with_hint(self):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data({"name": "x", "min_core_verison": "1.0.0"})
        message = str(excinfo.value)
        assert "min_core_verison" in message
        assert "did you mean 'min_core_version'?" in message

    @pytest.mark.parametrize("key", DOWNSTREAM_KEYS)
    def test_unprefixed_vendor_key_still_refused(self, key: str):
        with pytest.raises(ManifestValidationError):
            validate_manifest_data({"name": "x", key: {"a": 1}})

    def test_prefix_does_not_smuggle_in_a_bad_core_value(self):
        """An ``x-`` key is inert: it cannot set a core field by proximity."""
        model = validate_manifest_data({"name": "demo", "x-min_core_version": "9.9.9"})
        assert model.min_core_version is None
        assert model.extensions == {"x-min_core_version": "9.9.9"}


class TestErrorMessageIsDiscoverable:
    """A plugin author must learn the rule from the refusal alone."""

    def test_message_names_the_prefix_and_the_legal_spelling(self):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data({"name": "x", "control": {"a": 1}})
        message = str(excinfo.value)
        assert "'x-control'" in message
        assert "x-" in message

    def test_message_explains_what_happens_to_the_data(self):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data({"name": "x", "news_feeds": []})
        message = str(excinfo.value)
        assert "extensions" in message.lower()

    def test_uppercase_prefix_is_corrected_in_the_message(self):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data({"name": "x", "X-Control": 1})
        assert "'x-control'" in str(excinfo.value)

    def test_underscore_prefix_is_corrected_in_the_message(self):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data({"name": "x", "x_control": 1})
        assert "'x-control'" in str(excinfo.value)

    def test_loader_refusal_reason_carries_the_rule(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\ncontrol:\n  mode: on\n", encoding="utf-8"
        )
        reason = describe_manifest_failure(manifest)
        assert "x-control" in reason


class TestExtensionsArePreserved:
    @pytest.mark.parametrize("key", DOWNSTREAM_KEYS)
    def test_prefixed_key_validates(self, key: str):
        model = validate_manifest_data(
            {"name": "demo", f"{VENDOR_EXTENSION_PREFIX}{key}": {"nested": [1, 2]}}
        )
        assert model.extensions == {f"x-{key}": {"nested": [1, 2]}}

    def test_extensions_default_to_empty(self):
        assert validate_manifest_data({"name": "demo"}).extensions == {}

    def test_value_shape_is_unconstrained(self):
        model = validate_manifest_data(
            {
                "name": "demo",
                "x-scalar": 3,
                "x-list": [1, "two"],
                "x-null": None,
                "x-deep": {"a": {"b": ["c"]}},
            }
        )
        assert model.extensions["x-scalar"] == 3
        assert model.extensions["x-null"] is None
        assert model.extensions["x-deep"] == {"a": {"b": ["c"]}}

    def test_extension_reaches_plugin_metadata(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\nx-control:\n  poll_seconds: 30\n",
            encoding="utf-8",
        )
        metadata = PluginMetadata.from_file(manifest)
        assert metadata.extensions == {"x-control": {"poll_seconds": 30}}

    def test_owning_plugin_can_look_it_up_either_spelling(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\nx-news_feeds: [a, b]\n", encoding="utf-8"
        )
        metadata = PluginMetadata.from_file(manifest)
        assert metadata.extension("news_feeds") == ["a", "b"]
        assert metadata.extension("x-news_feeds") == ["a", "b"]
        assert metadata.extension("absent") is None
        assert metadata.extension("absent", default=[]) == []

    def test_metadata_serialization_round_trips(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\nx-runtime_prerequisites: [redis]\n",
            encoding="utf-8",
        )
        payload = PluginMetadata.from_file(manifest).to_dict()
        assert payload["x-runtime_prerequisites"] == ["redis"]

        # …and what to_dict() emits is still a legal manifest.
        written = tmp_path / "out.json"
        written.write_text(json.dumps(payload), encoding="utf-8")
        reloaded = PluginMetadata.from_file(written)
        assert reloaded.extension("runtime_prerequisites") == ["redis"]


class TestExtensionKeyShape:
    @pytest.mark.parametrize("key", ["x-", "x-  ", "x-!!", "x-a b"])
    def test_malformed_extension_key_refused(self, key: str):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data({"name": "demo", key: 1})
        assert "x-" in str(excinfo.value)

    @pytest.mark.parametrize(
        "key", ["x-a", "x-control", "x-news_feeds", "x-acme.co/thing-2"]
    )
    def test_reasonable_extension_keys_accepted(self, key: str):
        assert validate_manifest_data({"name": "demo", key: 1}).extensions[key] == 1

    def test_helper_agrees_with_the_prefix(self):
        assert is_extension_key("x-control")
        assert not is_extension_key("control")
        assert not is_extension_key("X-control")
        assert not is_extension_key(12)


class TestNoSecondDoor:
    """``extensions`` is derived from the ``x-`` keys, never declared directly."""

    def test_extensions_is_not_a_manifest_key(self):
        assert "extensions" not in known_manifest_keys()

    def test_declaring_extensions_directly_is_refused(self):
        with pytest.raises(ManifestValidationError):
            validate_manifest_data({"name": "demo", "extensions": {"x-a": 1}})

    def test_model_constructor_still_forbids_extras(self):
        with pytest.raises(Exception):
            PluginManifestModel(name="x", **{"x-control": 1})  # type: ignore[arg-type]


class TestExtensionsAreSigned:
    """Extension data must be inside the V5 digest, not a hole in it."""

    def _write_manifest(self, plugin_dir: Path, body: str) -> None:
        (plugin_dir / "manifest.yaml").write_text(body, encoding="utf-8")

    def test_adding_an_extension_changes_the_digest(self, plugin_dir: Path):
        before = compute_plugin_hash(plugin_dir)
        self._write_manifest(
            plugin_dir, "name: demo\nversion: 1.0.0\nx-control: {mode: read}\n"
        )
        assert compute_plugin_hash(plugin_dir) != before

    def test_editing_an_extension_changes_the_digest(self, plugin_dir: Path):
        self._write_manifest(
            plugin_dir, "name: demo\nversion: 1.0.0\nx-control: {mode: read}\n"
        )
        before = compute_plugin_hash(plugin_dir)
        self._write_manifest(
            plugin_dir, "name: demo\nversion: 1.0.0\nx-control: {mode: write}\n"
        )
        assert compute_plugin_hash(plugin_dir) != before

    def test_removing_an_extension_changes_the_digest(self, plugin_dir: Path):
        self._write_manifest(
            plugin_dir, "name: demo\nversion: 1.0.0\nx-control: {mode: read}\n"
        )
        before = compute_plugin_hash(plugin_dir)
        self._write_manifest(plugin_dir, "name: demo\nversion: 1.0.0\n")
        assert compute_plugin_hash(plugin_dir) != before

    def test_hash_injection_still_works_with_extensions(self, plugin_dir: Path):
        body = "name: demo\nversion: 1.0.0\nx-control: {mode: read}\n"
        self._write_manifest(plugin_dir, body)
        digest = compute_plugin_hash(plugin_dir)
        self._write_manifest(
            plugin_dir,
            body + f"integrity_sha256: {digest}\nhash_surface_version: 5\n",
        )
        assert compute_plugin_hash(plugin_dir) == digest

    def test_signed_tree_with_extensions_validates_and_loads(self, plugin_dir: Path):
        body = "name: demo\nversion: 1.0.0\nx-control: {mode: read}\n"
        self._write_manifest(plugin_dir, body)
        digest = compute_plugin_hash(plugin_dir)
        self._write_manifest(plugin_dir, body + f"integrity_sha256: {digest}\n")
        metadata = PluginMetadata.from_file(plugin_dir / "manifest.yaml")
        assert metadata.integrity_sha256 == digest
        assert metadata.extension("control") == {"mode": "read"}


class TestShippedManifestsUnaffected:
    def test_repo_manifests_declare_no_extensions_yet(self):
        for plugin_dir in sorted((REPO_ROOT / "plugins").iterdir()):
            manifest = plugin_dir / "manifest.yaml"
            if not manifest.is_file():
                continue
            model = load_manifest_model(manifest)
            assert isinstance(model.extensions, dict)
