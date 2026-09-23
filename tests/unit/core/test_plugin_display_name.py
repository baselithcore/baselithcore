"""Tests for the manifest ``display_name`` — a presentation-only plugin name.

``name`` is the identifier every route, config key, RBAC grant and table is
keyed by, so it can never be renamed for looks. ``display_name`` is the escape
hatch: consoles and the Backstage catalog show it, and nothing else changes.
These tests pin that it is accepted, carried, round-tripped and preferred as the
catalog title, and that a plugin declaring none keeps the old slug title.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from backstage_test_utils import make_plugin, make_provider

from core.plugins._metadata import PluginMetadata
from core.plugins.exporters.component_entity import plugin_title
from core.plugins.manifest_model import validate_manifest_data


def test_manifest_accepts_display_name() -> None:
    model = validate_manifest_data(
        {"name": "cv_intake", "version": "1.0.0", "display_name": "CV Intake"}
    )
    assert model.display_name == "CV Intake"
    assert model.name == "cv_intake"


def test_manifest_display_name_defaults_to_empty() -> None:
    model = validate_manifest_data({"name": "cv_intake", "version": "1.0.0"})
    assert model.display_name == ""


def test_metadata_carries_display_name_and_leaves_name_alone() -> None:
    model = validate_manifest_data(
        {"name": "cv_intake", "version": "1.0.0", "display_name": "  CV Intake "}
    )
    meta = PluginMetadata.from_model(model)
    assert meta.display_name == "CV Intake"
    assert meta.name == "cv_intake"


def test_metadata_without_display_name_is_empty() -> None:
    assert PluginMetadata(name="aura", version="1.0.0").display_name == ""


def test_to_dict_round_trips_through_the_manifest_schema() -> None:
    meta = PluginMetadata(name="cv_intake", version="1.0.0", display_name="CV Intake")
    data = meta.to_dict()
    assert data["display_name"] == "CV Intake"
    again = PluginMetadata.from_model(validate_manifest_data(data))
    assert again.display_name == "CV Intake"


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (SimpleNamespace(name="cv_intake", display_name="CV Intake"), "CV Intake"),
        (SimpleNamespace(name="cv_intake", display_name=""), "Cv Intake"),
        (SimpleNamespace(name="cv_intake", display_name="   "), "Cv Intake"),
        (SimpleNamespace(name="coding-agent"), "Coding Agent"),
    ],
)
def test_plugin_title_prefers_display_name(meta: object, expected: str) -> None:
    assert plugin_title(meta) == expected


def test_plugin_title_ignores_a_non_string_display_name() -> None:
    # Duck-typed metadata (mocks, third-party shims) may answer any attribute.
    assert plugin_title(SimpleNamespace(name="aura", display_name=42)) == "Aura"


@pytest.mark.asyncio
async def test_catalog_title_uses_display_name() -> None:
    plugin = make_plugin(name="cv_intake")
    plugin.metadata.display_name = "CV Intake"
    entity = await make_provider().to_catalog_info(plugin)
    assert entity["metadata"]["title"] == "CV Intake"
    # The entity identity is still the registry name, so the catalog keeps
    # the same Component and every relation pointing at it.
    assert entity["metadata"]["name"] == "cv_intake"


@pytest.mark.asyncio
async def test_catalog_title_falls_back_to_slug_title() -> None:
    entity = await make_provider().to_catalog_info(make_plugin(name="my-plugin"))
    assert entity["metadata"]["title"] == "My Plugin"
