"""verify_signed_assets: the signed download/verify pipeline as one call."""

from __future__ import annotations

import json
from pathlib import Path

from core.plugin_updates.cache import UpdateCache
from core.plugin_updates.models import Refusal
from core.plugin_updates.signed_assets import verify_signed_assets
from core.plugins.signing import generate_keypair_hex

from .test_checker import _info, _signed_tarball, _Source


async def _verify(tmp_path: Path, src: _Source, keys: list[str]):
    return await verify_signed_assets(
        "demo",
        _info(),
        "1.1.0",
        source=src,  # type: ignore[arg-type]
        cache=UpdateCache(tmp_path / "c"),
        core_version="1.50.0",
        trusted_keys=keys,
    )


async def test_verified_assets_pin_the_tarball(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    assets = await _verify(tmp_path, src, [pub])
    assert assets.verified and assets.refusal is None
    assert assets.tarball_sha256 == meta["tarball_sha256"]
    assert assets.files_count == len(meta["files"])
    assert assets.host_build_required is False
    assert UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0").is_file()


async def test_foreign_key_is_refused_and_uncached(tmp_path: Path) -> None:
    priv, _ = generate_keypair_hex()
    _, other = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    assets = await _verify(tmp_path, src, [other])
    assert not assets.verified and assets.refusal is Refusal.SIGNATURE_INVALID
    assert not UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0").exists()


async def test_host_build_flag_is_read_from_the_verified_manifest(
    tmp_path: Path,
) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(
        tmp_path, priv, extra_manifest="host_build_required: true\n"
    )
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    assets = await _verify(tmp_path, src, [pub])
    assert assets.verified and assets.host_build_required is True
