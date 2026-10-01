from __future__ import annotations

from core.plugins.manifest_model import validate_manifest_data


def test_defaults_to_false() -> None:
    model = validate_manifest_data({"name": "demo", "version": "1.0.0"})
    assert model.host_build_required is False


def test_true_is_kept() -> None:
    model = validate_manifest_data(
        {"name": "demo", "version": "1.0.0", "host_build_required": True}
    )
    assert model.host_build_required is True
