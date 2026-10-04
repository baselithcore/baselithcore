#!/usr/bin/env python3
"""Smoke-test an installed ``baselith-core`` the way a user's install sees it.

``check_distribution_artifacts.py`` inspects the wheel's *contents*. What it
cannot see is the dependency metadata: in CI the wheel was installed over the
hash-locked runtime set, so a module the code imports but ``pyproject.toml``
never declares (``greenlet``, reached only through ``sqlalchemy[asyncio]``) was
always present and the gap only surfaced on a user's ``pip install``.

Run this with the interpreter of a fresh virtualenv into which only the wheel
was installed, resolved by pip from its declared requirements and nothing else::

    python -m venv /tmp/v && /tmp/v/bin/pip install dist/*.whl
    /tmp/v/bin/python scripts/smoke_installed_wheel.py

It moves to a temporary working directory (so the checkout cannot shadow the
installed package) and runs, in order:

``import``   ``import baselith`` and ``from baselith import *`` resolve every
             public name, from the installed copy, not the checkout.
``cli``      ``baselith --help`` exits 0.
``migrate``  ``baselith db migrate`` applies the packaged migrations to the
             database the ``DB_*`` environment names.
``boot``     ``uvicorn core.api.factory:create_app --factory`` starts, and
             ``/health`` and ``/health/ready`` answer 200 with the database
             reported up (``--require-redis`` also demands Redis).

Exit 0 when every requested check passes, 1 at the first failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKS: tuple[str, ...] = ("import", "cli", "migrate", "boot")


class SmokeError(RuntimeError):
    """One check failed; the message says which and why."""


def _bin(name: str) -> str:
    """A console script installed next to the running interpreter."""
    return str(Path(sys.executable).parent / name)


def assert_outside(location: str | os.PathLike[str], root: Path = REPO_ROOT) -> None:
    """Fail when ``location`` lies inside the checkout at ``root``.

    An import that resolves into the source tree proves nothing about the
    wheel: it is the checkout answering, with every file the wheel may lack.
    """
    path = Path(location).resolve()
    if path == root or root in path.parents:
        raise SmokeError(
            f"{path} resolves inside the checkout {root}; run from outside it, "
            "without PYTHONPATH, in a venv where only the wheel is installed"
        )


def check_import(root: Path = REPO_ROOT) -> str:
    """Import the facade and every name in its ``__all__``; return its path."""
    import importlib

    module = importlib.import_module("baselith")
    assert_outside(str(module.__file__), root)
    namespace: dict[str, Any] = {}
    exec("from baselith import *", namespace)  # noqa: S102  # nosec B102
    missing = sorted(set(module.__all__) - set(namespace))
    if missing:
        raise SmokeError(f"`from baselith import *` did not bind {missing}")
    core = importlib.import_module("core")
    assert_outside(str(core.__file__), root)
    return str(module.__file__)


def _run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), capture_output=True, text=True, timeout=timeout, check=False
    )


def check_cli(timeout: float = 60) -> None:
    """``baselith --help`` exits 0 and prints a usage line."""
    result = _run([_bin("baselith"), "--help"], timeout)
    if result.returncode != 0 or "usage" not in result.stdout.lower():
        raise SmokeError(
            f"`baselith --help` exited {result.returncode}:\n"
            f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )


def check_migrate(timeout: float = 180) -> None:
    """``baselith db migrate`` exits 0 against the configured database."""
    result = _run([_bin("baselith"), "db", "migrate"], timeout)
    if result.returncode != 0:
        raise SmokeError(
            f"`baselith db migrate` exited {result.returncode}:\n"
            f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
        )


Fetch = Callable[[str], tuple[int, bytes]]


def fetch(url: str) -> tuple[int, bytes]:
    """GET ``url``; return status and body, including for 4xx/5xx answers."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310  # nosec B310
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


def wait_for(
    url: str,
    timeout: float,
    *,
    get: Fetch = fetch,
    alive: Callable[[], bool] = lambda: True,
    interval: float = 1.0,
) -> tuple[int, bytes]:
    """Poll ``url`` until it answers 200, the server dies, or ``timeout`` passes.

    Returns the last answer, so the caller reports a 503 body rather than a
    bare timeout.
    """
    deadline = time.monotonic() + timeout
    last: tuple[int, bytes] = (0, b"no answer")
    while time.monotonic() < deadline:
        if not alive():
            raise SmokeError(f"the server exited before {url} answered")
        try:
            last = get(url)
        except (OSError, urllib.error.URLError) as exc:
            last = (0, str(exc).encode())
        if last[0] == 200:
            return last
        time.sleep(interval)
    return last


def assert_ready(status: int, body: bytes, *, require_redis: bool) -> None:
    """``/health/ready`` answered 200 with the database (and Redis) up."""
    if status != 200:
        raise SmokeError(f"/health/ready answered {status}: {body[:2000]!r}")
    services = json.loads(body).get("services", {})
    wanted = ["database", "redis"] if require_redis else ["database"]
    down = [name for name in wanted if services.get(name) is not True]
    if down:
        raise SmokeError(f"/health/ready reports {down} down: {services}")


def check_boot(port: int, timeout: float, *, require_redis: bool) -> None:
    """Start the app under uvicorn and probe its health endpoints."""
    log = Path(tempfile.mkstemp(prefix="smoke-uvicorn-", suffix=".log")[1])
    argv = [
        sys.executable,
        "-m",
        "uvicorn",
        "core.api.factory:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    with log.open("w", encoding="utf-8") as out:
        server = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT)  # nosec B603
    base = f"http://127.0.0.1:{port}"
    try:
        alive = lambda: server.poll() is None  # noqa: E731
        status, body = wait_for(f"{base}/health", timeout, alive=alive)
        if status != 200:
            raise SmokeError(f"/health answered {status}: {body[:2000]!r}")
        status, body = wait_for(f"{base}/health/ready", 30, alive=alive)
        assert_ready(status, body, require_redis=require_redis)
    except SmokeError as exc:
        tail = log.read_text(encoding="utf-8", errors="replace")[-6000:]
        raise SmokeError(f"{exc}\n--- server log (tail) ---\n{tail}") from exc
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--checks",
        default=",".join(CHECKS),
        help=f"comma-separated subset of {', '.join(CHECKS)} (default: all)",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--require-redis", action="store_true")
    args = parser.parse_args(argv)
    args.checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    unknown = sorted(set(args.checks) - set(CHECKS))
    if unknown:
        parser.error(f"unknown check(s): {unknown}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    # The checkout must not be importable: drop this script's directory (which
    # Python put first on sys.path) and leave the tree.
    sys.path[:] = [
        p for p in sys.path if Path(p or ".").resolve() != REPO_ROOT / "scripts"
    ]
    os.chdir(tempfile.mkdtemp(prefix="baselith-smoke-"))
    steps: dict[str, Callable[[], object]] = {
        "import": check_import,
        "cli": check_cli,
        "migrate": check_migrate,
        "boot": lambda: check_boot(
            args.port, args.timeout, require_redis=args.require_redis
        ),
    }
    for name in args.checks:
        started = time.monotonic()
        try:
            detail = steps[name]()
        except (SmokeError, subprocess.TimeoutExpired, ImportError) as exc:
            print(f"FAIL {name}: {exc}", file=sys.stderr)
            return 1
        suffix = f" ({detail})" if isinstance(detail, str) else ""
        print(f"ok   {name} [{time.monotonic() - started:.1f}s]{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
