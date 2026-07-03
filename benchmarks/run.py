#!/usr/bin/env python
"""
Microbenchmarks for BaselithCore request-path primitives.

Measures the throughput of the small, hot, in-process operations the framework
runs on nearly every request — prompt rendering, scope checks, webhook signing,
cursor pagination, per-tenant key derivation, field encryption, and JSON
serialization. No network, database, or LLM calls, so results are deterministic
and reproducible anywhere:

    python benchmarks/run.py            # human-readable table
    python benchmarks/run.py --markdown # Markdown table (for docs)

Numbers are single-machine and indicative — use them for relative comparison and
regression spotting, not as absolute guarantees. Reproduce on your own hardware.
"""

from __future__ import annotations

import sys
import time
from typing import Callable


def _bench(fn: Callable[[], object], iterations: int) -> tuple[float, float]:
    """Return (ops_per_sec, microseconds_per_op) for ``fn`` over ``iterations``."""
    fn()  # warm up (imports, caches)
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    elapsed = time.perf_counter() - start
    ops_per_sec = iterations / elapsed if elapsed > 0 else float("inf")
    us_per_op = (elapsed / iterations) * 1_000_000 if iterations else 0.0
    return ops_per_sec, us_per_op


def _cases() -> list[tuple[str, Callable[[], object], int]]:
    """Build the (name, callable, iterations) benchmark cases."""
    from core.api.pagination import paginate_sequence
    from core.auth.scopes import scope_satisfied
    from core.prompts.rendering import render_template
    from core.security.encryption import FieldEncryptor
    from core.tenancy.encryption import derive_tenant_key_material
    from core.webhooks.signing import build_signature_header

    # Prompt rendering
    template = "Hello {{ name }}, welcome to {{ product }} ({{ tier }} tier)."
    variables = {"name": "Gio", "product": "BaselithCore", "tier": "pro"}

    # Scope matching
    granted = {"chat:*", "memory:read", "metrics:read"}

    # Webhook signing
    body = b'{"id":"evt_1","type":"chat.completed","data":{"k":"v"}}'

    # Pagination
    seq = list(range(1000))

    # Field encryption (build once; bench encrypt+decrypt roundtrip)
    enc = FieldEncryptor.from_keys({"k1": "a-strong-operator-passphrase"}, "k1")

    def _encrypt_roundtrip() -> None:
        enc.decrypt(enc.encrypt("sensitive-field-value"))

    # JSON serialization (orjson vs stdlib) on a representative payload
    import json

    import orjson

    payload = {"answer": "x" * 200, "sources": [{"id": i} for i in range(20)]}

    return [
        (
            "prompt render ({{var}})",
            lambda: render_template(template, variables),
            200_000,
        ),
        (
            "scope match (wildcard)",
            lambda: scope_satisfied(granted, "chat:write"),
            1_000_000,
        ),
        ("webhook HMAC sign", lambda: build_signature_header("whsec_x", body), 200_000),
        ("cursor paginate (1k seq)", lambda: paginate_sequence(seq, limit=50), 200_000),
        (
            "per-tenant key derive (HKDF)",
            lambda: derive_tenant_key_material("base", "t"),
            50_000,
        ),
        ("field encrypt+decrypt (AES-GCM)", _encrypt_roundtrip, 100_000),
        ("orjson dumps", lambda: orjson.dumps(payload), 200_000),
        ("stdlib json.dumps", lambda: json.dumps(payload), 200_000),
    ]


def main(argv: list[str]) -> int:
    markdown = "--markdown" in argv
    results: list[tuple[str, float, float]] = []
    for name, fn, iters in _cases():
        ops, us = _bench(fn, iters)
        results.append((name, ops, us))

    if markdown:
        print("| Operation | Throughput (ops/sec) | Latency (µs/op) |")
        print("| --------- | -------------------: | --------------: |")
        for name, ops, us in results:
            print(f"| {name} | {ops:,.0f} | {us:.3f} |")
    else:
        width = max(len(n) for n, _, _ in results)
        print(f"{'Operation':<{width}}  {'ops/sec':>14}  {'µs/op':>10}")
        print("-" * (width + 28))
        for name, ops, us in results:
            print(f"{name:<{width}}  {ops:>14,.0f}  {us:>10.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
