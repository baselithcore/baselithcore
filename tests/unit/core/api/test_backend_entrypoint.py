"""The ``python backend.py`` path must serve with the same posture as Docker.

The Dockerfile runs uvicorn with ``--proxy-headers``, ``--forwarded-allow-ips``
and ``--timeout-graceful-shutdown``; the in-process entrypoint did not. Behind a
proxy that meant ``request.client.host`` was the load balancer for every caller
— collapsing the per-IP rate limiter, the failed-auth throttle and the admin
lockout into one shared bucket — and SIGTERM cut open SSE streams dead instead
of draining them.

Parsed from source rather than executed: importing ``backend`` builds the whole
FastAPI app at module scope, which a unit test has no business doing.
"""

from __future__ import annotations

import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[4] / "backend.py"


def _uvicorn_run_call() -> ast.Call:
    tree = ast.parse(BACKEND.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "run"
            and isinstance(func.value, ast.Name)
            and func.value.id == "uvicorn"
        ):
            return node
    raise AssertionError("no uvicorn.run(...) call in backend.py")


def _kwargs() -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in _uvicorn_run_call().keywords if kw.arg}


def test_proxy_headers_are_trusted_explicitly() -> None:
    node = _kwargs().get("proxy_headers")
    assert isinstance(node, ast.Constant) and node.value is True


def test_forwarded_allow_ips_comes_from_the_dockerfile_variable() -> None:
    """Same knob as the container CMD, so the two entrypoints cannot diverge."""
    node = _kwargs().get("forwarded_allow_ips")
    assert node is not None, "forwarded_allow_ips not passed"
    assert "FORWARDED_ALLOW_IPS" in ast.dump(node)

    docker_cmd = (BACKEND.parent / "Dockerfile").read_text(encoding="utf-8")
    assert "FORWARDED_ALLOW_IPS" in docker_cmd


def test_shutdown_drain_is_bounded_by_the_dockerfile_variable() -> None:
    """Same knob as the container CMD, defaulting to the same 30s."""
    node = _kwargs().get("timeout_graceful_shutdown")
    assert node is not None, "timeout_graceful_shutdown not passed"
    dumped = ast.dump(node)
    assert "GRACEFUL_SHUTDOWN_TIMEOUT" in dumped
    assert "'30'" in dumped, f"default must stay 30: {dumped}"

    docker_cmd = (BACKEND.parent / "Dockerfile").read_text(encoding="utf-8")
    assert "GRACEFUL_SHUTDOWN_TIMEOUT" in docker_cmd
