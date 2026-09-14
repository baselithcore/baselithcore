"""Tests for the strict plugin-manifest schema.

The manifest is the plugin contract; before ``PluginManifestModel`` it was read
with ``data.get(...)`` and a misspelled key vanished silently. These tests pin
both halves of the fix: unknown keys are rejected with a useful message, and no
manifest actually shipped in this repo is rejected by the new strictness.
"""

from pathlib import Path

import pytest

from core.plugins._metadata import PluginMetadata
from core.plugins.manifest_model import (
    ManifestValidationError,
    PluginManifestModel,
    known_manifest_keys,
    load_manifest_model,
    validate_manifest_data,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGINS_DIR = REPO_ROOT / "plugins"


def _shipped_manifests() -> list[Path]:
    found: list[Path] = []
    for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith((".", "_")):
            continue
        for filename in ("manifest.yaml", "manifest.yml", "manifest.json"):
            candidate = plugin_dir / filename
            if candidate.exists():
                found.append(candidate)
                break
    return found


class TestShippedManifests:
    """The strict model must not reject anything this repo already ships."""

    def test_repo_has_manifests_to_check(self):
        assert _shipped_manifests(), "no plugin manifests found to validate"

    @pytest.mark.parametrize(
        "manifest_path", _shipped_manifests(), ids=lambda p: p.parent.name
    )
    def test_shipped_manifest_validates(self, manifest_path: Path):
        model = load_manifest_model(manifest_path)
        assert model.name

    @pytest.mark.parametrize(
        "manifest_path", _shipped_manifests(), ids=lambda p: p.parent.name
    )
    def test_shipped_manifest_builds_metadata(self, manifest_path: Path):
        metadata = PluginMetadata.from_file(manifest_path)
        assert metadata.name
        assert metadata.version


class TestUnknownKeys:
    def test_typo_names_key_and_nearest_match(self):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data(
                {"name": "x", "version": "1.0.0", "min_core_verison": "1.0.0"}
            )
        message = str(excinfo.value)
        assert "min_core_verison" in message
        assert "min_core_version" in message

    def test_unrelated_key_still_named(self):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data({"name": "x", "zzz_qqq_wibble": 1})
        message = str(excinfo.value)
        assert "zzz_qqq_wibble" in message
        assert "did you mean" not in message

    def test_all_unknown_keys_reported(self):
        with pytest.raises(ManifestValidationError) as excinfo:
            validate_manifest_data({"autor": "a", "licence": "MIT"})
        message = str(excinfo.value)
        assert "autor" in message
        assert "licence" in message

    def test_model_itself_forbids_extras(self):
        with pytest.raises(Exception):
            PluginManifestModel(name="x", nope=1)  # type: ignore[call-arg]

    def test_from_file_rejects_unknown_key(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("name: x\nversion: 1.0.0\nauthr: me\n", encoding="utf-8")
        with pytest.raises(ManifestValidationError):
            PluginMetadata.from_file(manifest)


class TestAcceptedShapes:
    def test_empty_manifest_is_valid(self):
        model = validate_manifest_data({})
        assert model.name == ""
        assert model.version == "0.1.0"

    def test_none_manifest_is_valid(self):
        assert validate_manifest_data(None).version == "0.1.0"

    def test_non_mapping_rejected(self):
        with pytest.raises(ManifestValidationError):
            validate_manifest_data(["not", "a", "mapping"])

    def test_numeric_version_coerced_to_string(self):
        assert validate_manifest_data({"version": 1.0}).version == "1.0"

    def test_null_core_bounds_allowed(self):
        model = validate_manifest_data(
            {"min_core_version": None, "max_core_version": None}
        )
        assert model.min_core_version is None

    def test_rich_environment_variables_allowed(self):
        model = validate_manifest_data(
            {"environment_variables": [{"name": "FOO", "required": True}, "BAR"]}
        )
        assert len(model.environment_variables or []) == 2

    def test_known_keys_cover_documented_surface(self):
        keys = known_manifest_keys()
        for expected in (
            "entry_point",
            "integrity_sha256",
            "signature_ed25519",
            "permissions",
            "python_dependencies",
            "min_core_version",
            "readiness",
            "hash_surface_version",
            "id",
            "repository",
        ):
            assert expected in keys

    def test_malformed_yaml_raises_manifest_error(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("name: [unclosed\n", encoding="utf-8")
        with pytest.raises(ManifestValidationError):
            load_manifest_model(manifest)


class TestTolerantShapes:
    """The model polices the key set — not YAML whitespace habits."""

    def test_blank_scalar_falls_back_to_the_field_default(self):
        model = validate_manifest_data(
            {
                "name": "demo",
                "description": None,
                "author": None,
                "version": None,
                "category": None,
                "readiness": None,
                "system": None,
            }
        )
        assert model.description == ""
        assert model.author == ""
        assert model.version == "0.1.0"
        assert model.category == "Generic"
        assert model.readiness == "stable"
        assert model.system is False

    def test_blank_manifest_body_parses(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\ndescription:\nicon:\ntags:\n",
            encoding="utf-8",
        )
        metadata = PluginMetadata.from_file(manifest)
        assert metadata.description == ""
        assert metadata.tags == []

    @pytest.mark.parametrize(
        "field",
        [
            "tags",
            "python_dependencies",
            "dependencies",
            "required_resources",
            "optional_resources",
            "screenshots",
            "environment_variables",
        ],
    )
    def test_scalar_is_read_as_a_one_item_list(self, field: str):
        model = validate_manifest_data({"name": "demo", field: "solo"})
        assert getattr(model, field) == ["solo"]

    def test_scalar_list_reaches_metadata(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\ntags: example\npython_dependencies: httpx>=0.27\n",
            encoding="utf-8",
        )
        metadata = PluginMetadata.from_file(manifest)
        assert metadata.tags == ["example"]
        assert metadata.python_dependencies == ["httpx>=0.27"]

    def test_tolerance_does_not_weaken_the_key_check(self):
        with pytest.raises(ManifestValidationError):
            validate_manifest_data({"name": "demo", "tagz": None})


class TestMetadataFromModel:
    def test_entry_point_exposed(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\nentry_point: plugin:DemoPlugin\n",
            encoding="utf-8",
        )
        metadata = PluginMetadata.from_file(manifest)
        assert metadata.entry_point == "plugin:DemoPlugin"

    def test_identity_extras_exposed(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\nid: demo-id\nrepository: https://example.test\n",
            encoding="utf-8",
        )
        metadata = PluginMetadata.from_file(manifest)
        assert metadata.plugin_id == "demo-id"
        assert metadata.repository == "https://example.test"

    def test_from_model_roundtrip(self):
        model = validate_manifest_data(
            {"name": "demo", "version": "2.1.0", "tenancy": "personal"}
        )
        metadata = PluginMetadata.from_model(model)
        assert metadata.name == "demo"
        assert metadata.tenancy == "personal"

    def test_to_dict_includes_entry_point(self, tmp_path: Path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "name: demo\nversion: 1.0.0\nentry_point: plugin:DemoPlugin\n",
            encoding="utf-8",
        )
        payload = PluginMetadata.from_file(manifest).to_dict()
        assert payload["entry_point"] == "plugin:DemoPlugin"
