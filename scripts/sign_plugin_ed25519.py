#!/usr/bin/env python3
"""Ed25519 publisher signing for plugins.

Usage:
    python scripts/sign_plugin_ed25519.py keygen
        Prints a fresh keypair. Store the private key in your secret manager;
        distribute the public key via BASELITH_PLUGIN_TRUST_ROOTS.

    python scripts/sign_plugin_ed25519.py sign plugins/<name> --key-env SIGNING_KEY
        Recomputes the plugin integrity hash, signs it with the hex private
        key read from the given environment variable (never from argv, so the
        key does not leak into shell history / process listings), and writes
        both integrity_sha256 and signature_ed25519 into the manifest.

Verification is enforced at load time when BASELITH_REQUIRE_PLUGIN_SIGNATURES
is enabled (see core/plugins/signing.py).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.plugins.integrity import compute_plugin_hash
from core.plugins.signing import (
    generate_keypair_hex,
    sign_plugin_hash,
    verify_plugin_signature,
)


def _find_manifest(plugin_dir: Path) -> Path:
    for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
        candidate = plugin_dir / name
        if candidate.exists():
            return candidate
    raise SystemExit(f"error: no manifest found in {plugin_dir}")


def _write_manifest_fields(manifest: Path, hash_hex: str, signature_hex: str) -> None:
    if manifest.suffix == ".json":
        import json

        data = json.loads(manifest.read_text())
        data["integrity_sha256"] = hash_hex
        data["signature_ed25519"] = signature_hex
        manifest.write_text(json.dumps(data, indent=2) + "\n")
        return

    import re

    text = manifest.read_text()
    for key, value in (
        ("integrity_sha256", hash_hex),
        ("signature_ed25519", signature_hex),
    ):
        pattern = re.compile(rf"^{key}:.*$", flags=re.MULTILINE)
        line = f"{key}: {value}"
        if pattern.search(text):
            text = pattern.sub(line, text)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    manifest.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("keygen", help="generate and print a new keypair")
    sign = sub.add_parser("sign", help="sign a plugin directory's manifest")
    sign.add_argument("plugin_dir", type=Path)
    sign.add_argument(
        "--key-env",
        default="BASELITH_PLUGIN_SIGNING_KEY",
        help="env var holding the hex Ed25519 private key (default: %(default)s)",
    )
    args = parser.parse_args()

    if args.command == "keygen":
        private_hex, public_hex = generate_keypair_hex()
        print(f"private (keep secret): {private_hex}")
        print(f"public  (trust root):  {public_hex}")
        return 0

    private_hex = os.environ.get(args.key_env, "").strip()
    if not private_hex:
        print(f"error: environment variable {args.key_env} is empty", file=sys.stderr)
        return 1

    plugin_dir = args.plugin_dir.resolve()
    manifest = _find_manifest(plugin_dir)
    hash_hex = compute_plugin_hash(plugin_dir)
    signature_hex = sign_plugin_hash(hash_hex, private_hex)
    _write_manifest_fields(manifest, hash_hex, signature_hex)

    # Self-check with the derived public key before declaring success.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    public_hex = (
        Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
        .hex()
    )
    assert verify_plugin_signature(hash_hex, signature_hex, [public_hex])
    print(f"signed {plugin_dir.name}: integrity_sha256={hash_hex[:16]}…")
    print(f"trust root for deployments: {public_hex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
