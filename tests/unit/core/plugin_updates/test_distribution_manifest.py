"""distribution-release.json: a signed manifest naming the commit, the lock and the migrations."""

from __future__ import annotations

import json
from typing import Any

import pytest

from baselith.core.plugin_updates.distribution_manifest import (
    DISTRIBUTION_ASSET,
    DistributionManifest,
    DistributionRefused,
    build_manifest_meta,
    distribution_refusal,
    load_distribution_manifest,
)
from baselith.core.plugin_updates.models import Refusal
from baselith.core.plugin_updates.release_manifest import (
    SIGNATURE_KEY,
    ReleaseManifestError,
    sign_release_manifest,
)
from baselith.core.plugins.signing import generate_keypair_hex

NAME = "baselithcore-enterprise"
SHA = "a" * 40
LOCK = "b" * 64


def _manifest(**over: Any) -> DistributionManifest:
    fields: dict[str, Any] = {
        "name": NAME,
        "version": "1.15.0",
        "commit_sha": SHA,
        "lock_sha256": LOCK,
        "migrations": "none",
        "min_core_version": None,
    }
    fields.update(over)
    return DistributionManifest(**fields)


def _signed(meta: dict[str, Any], priv: str) -> dict[str, Any]:
    return {**meta, SIGNATURE_KEY: sign_release_manifest(meta, priv)}


def _raw(meta: dict[str, Any]) -> bytes:
    return json.dumps(meta).encode()


@pytest.fixture
def keys() -> tuple[str, str]:
    return generate_keypair_hex()


def _load(raw: bytes, pub: str, **over: str) -> DistributionManifest:
    return load_distribution_manifest(
        raw,
        [pub],
        expected_name=over.get("name", NAME),
        expected_version=over.get("version", "1.15.0"),
    )


def test_asset_name() -> None:
    assert DISTRIBUTION_ASSET == "distribution-release.json"


def test_valid_manifest_loads(keys: tuple[str, str]) -> None:
    priv, pub = keys
    meta = _signed(build_manifest_meta(_manifest()), priv)
    assert meta["release_format"] == 2 and meta["kind"] == "distribution"
    loaded = _load(_raw(meta), pub)
    assert loaded == _manifest()


def test_wrong_signature_refused(keys: tuple[str, str]) -> None:
    priv, _ = keys
    _, other = generate_keypair_hex()
    meta = _signed(build_manifest_meta(_manifest()), priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), other)
    assert caught.value.refusal is Refusal.SIGNATURE_INVALID
    assert str(caught.value) == "signature_invalid"


def test_unsigned_manifest_refused(keys: tuple[str, str]) -> None:
    _, pub = keys
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(build_manifest_meta(_manifest())), pub)
    assert caught.value.refusal is Refusal.SIGNATURE_INVALID


def test_no_trusted_keys_refused(keys: tuple[str, str]) -> None:
    priv, _ = keys
    meta = _signed(build_manifest_meta(_manifest()), priv)
    refusal, manifest = distribution_refusal(
        meta, [], expected_name=NAME, expected_version="1.15.0"
    )
    assert refusal is Refusal.NO_TRUSTED_KEYS and manifest is None


def test_name_mismatch_refused(keys: tuple[str, str]) -> None:
    priv, pub = keys
    meta = _signed(build_manifest_meta(_manifest(name="demo")), priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.NAME_MISMATCH


def test_version_mismatch_refused(keys: tuple[str, str]) -> None:
    priv, pub = keys
    meta = _signed(build_manifest_meta(_manifest(version="1.16.0")), priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.VERSION_MISMATCH


def test_kind_not_distribution_refused(keys: tuple[str, str]) -> None:
    priv, pub = keys
    meta = _signed({**build_manifest_meta(_manifest()), "kind": "plugin"}, priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.MANIFEST_INVALID


def test_release_format_must_be_2(keys: tuple[str, str]) -> None:
    priv, pub = keys
    meta = _signed({**build_manifest_meta(_manifest()), "release_format": 3}, priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.MANIFEST_INVALID


@pytest.mark.parametrize("sha", ["a" * 39, "A" * 40, "g" * 40, 7, "a" * 41])
def test_commit_sha_must_be_40_hex(keys: tuple[str, str], sha: Any) -> None:
    priv, pub = keys
    meta = _signed({**build_manifest_meta(_manifest()), "commit_sha": sha}, priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.MANIFEST_INVALID


@pytest.mark.parametrize("lock", ["b" * 63, "B" * 64, "", None])
def test_lock_sha256_must_be_64_hex(keys: tuple[str, str], lock: Any) -> None:
    priv, pub = keys
    meta = _signed({**build_manifest_meta(_manifest()), "lock_sha256": lock}, priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.MANIFEST_INVALID


@pytest.mark.parametrize("value", ["None", "contract", "", 0, None])
def test_migrations_must_be_a_known_value(keys: tuple[str, str], value: Any) -> None:
    priv, pub = keys
    meta = _signed({**build_manifest_meta(_manifest()), "migrations": value}, priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.MANIFEST_INVALID


def test_model_refuses_bad_values_directly() -> None:
    with pytest.raises(ValueError):
        _manifest(commit_sha="x")
    with pytest.raises(ValueError):
        _manifest(migrations="sometimes")


def test_extra_signed_keys_are_covered_by_the_signature(
    keys: tuple[str, str],
) -> None:
    priv, pub = keys
    meta = _signed(build_manifest_meta(_manifest(migrations="breaking")), priv)
    meta["migrations"] = "none"  # flipped after signing: the gate would open
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.SIGNATURE_INVALID


def test_duplicate_keys_and_floats_refused(keys: tuple[str, str]) -> None:
    priv, pub = keys
    meta = _signed(build_manifest_meta(_manifest()), priv)
    text = _raw(meta).decode()
    duplicated = text[:-1] + ', "migrations": "breaking"}'
    with pytest.raises(ReleaseManifestError, match="duplicate key"):
        _load(duplicated.encode(), pub)
    floated = text[:-1] + ', "extra": 1.5}'
    with pytest.raises(ReleaseManifestError, match="float"):
        _load(floated.encode(), pub)


def test_not_an_object_is_manifest_invalid(keys: tuple[str, str]) -> None:
    _, pub = keys
    with pytest.raises(ReleaseManifestError):
        _load(b"[1, 2]", pub)


def test_plugin_release_json_is_not_a_distribution_manifest(
    keys: tuple[str, str],
) -> None:
    priv, pub = keys
    plugin_meta = {
        "release_format": 2,
        "name": NAME,
        "version": "1.15.0",
        "tarball_sha256": "c" * 64,
        "files": {"__init__.py": "d" * 64},
    }
    meta = _signed(plugin_meta, priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.MANIFEST_INVALID


def test_refusal_returns_the_manifest_when_valid(keys: tuple[str, str]) -> None:
    priv, pub = keys
    meta = _signed(build_manifest_meta(_manifest(migrations="expand")), priv)
    refusal, manifest = distribution_refusal(
        meta, [pub], expected_name=NAME, expected_version="1.15.0"
    )
    assert refusal is None
    assert manifest is not None and manifest.migrations == "expand"


DIGEST = "sha256:" + "e" * 64


def test_image_digest_is_signed_and_carried(keys: tuple[str, str]) -> None:
    priv, pub = keys
    meta = _signed(build_manifest_meta(_manifest(image_digest=DIGEST)), priv)
    assert meta["image_digest"] == DIGEST
    loaded = _load(_raw(meta), pub)
    assert loaded.image_digest == DIGEST
    from baselith.core.plugin_updates.apply.drivers._core_guards import CoreRelease

    assert CoreRelease.of(loaded).image_digest == DIGEST
    floor = _load(
        _raw(_signed(build_manifest_meta(_manifest(min_core_version="1.3.0")), priv)),
        pub,
    )
    assert CoreRelease.of(floor).min_core_version == "1.3.0"
    swapped = {**meta, "image_digest": "sha256:" + "f" * 64}  # replaced after signing
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(swapped), pub)
    assert caught.value.refusal is Refusal.SIGNATURE_INVALID


def test_image_digest_is_optional(keys: tuple[str, str]) -> None:
    priv, pub = keys
    loaded = _load(_raw(_signed(build_manifest_meta(_manifest()), priv)), pub)
    assert loaded.image_digest is None


@pytest.mark.parametrize(
    "digest", ["sha256:" + "e" * 63, "sha256:" + "E" * 64, "md5:" + "e" * 64, "", 7]
)
def test_image_digest_must_be_a_sha256_digest(
    keys: tuple[str, str], digest: Any
) -> None:
    priv, pub = keys
    meta = _signed({**build_manifest_meta(_manifest()), "image_digest": digest}, priv)
    with pytest.raises(DistributionRefused) as caught:
        _load(_raw(meta), pub)
    assert caught.value.refusal is Refusal.MANIFEST_INVALID
