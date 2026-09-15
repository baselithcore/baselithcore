"""Start a standalone BaselithCore Docker runtime."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from core import __version__
from core.cli.commands.env_profiles import ensure_docker_core_env
from core.cli.templates.docker_runtime import DOCKER_RUNTIME_FILES
from core.cli.ui import (
    console,
    print_error,
    print_header,
    print_step,
    print_success,
)

DEFAULT_CORE_IMAGE = f"ghcr.io/baselithcore/baselithcore:{__version__}"
COMPOSE_FILE = "docker-compose.core.yml"
DOCKER_ENV_FILE = Path("configs/.env.docker.core")
DOCKER_BIN = shutil.which("docker")


def run_up(image: str | None = None, timeout: int = 300) -> int:
    """Prepare and start the complete standalone Docker stack."""
    root = Path.cwd()
    selected_image = (
        image or os.environ.get("BASELITH_CORE_IMAGE") or DEFAULT_CORE_IMAGE
    )

    print_header("Baselith Docker Runtime", "Core and backing services")
    if not _docker_available():
        print_error(
            "Docker is not reachable", "Start Docker and run baselith up again."
        )
        return 1

    try:
        created = _ensure_runtime_project(root)
        env_changes = ensure_docker_core_env(DOCKER_ENV_FILE)
    except (OSError, ValueError) as exc:
        print_error("Cannot prepare Docker runtime", str(exc))
        return 1

    if created:
        print_success("Runtime project prepared")
    if env_changes:
        print_success("Environment and credentials prepared")

    env = _compose_environment(selected_image)
    if _compose(["pull", "postgres", "redis", "qdrant"], env) != 0:
        print_error("Could not download backing service images")
        return 1
    print_success("PostgreSQL, FalkorDB and Qdrant images ready")

    if _compose(["build", "--pull", "api"], env) != 0:
        print_error("Could not prepare the BaselithCore image")
        return 1
    print_success(f"Core image ready: {selected_image}")

    if _compose(["up", "-d"], env) != 0:
        print_error("Could not start the Docker runtime")
        return 1
    print_success("Docker services started")

    port = env.get("BASELITH_HTTP_PORT", "8000")
    print_step("Waiting for BaselithCore health check...")
    if not _wait_for_health(port, timeout):
        _compose(["ps"], env)
        _compose(["logs", "--tail=100", "api"], env)
        print_error(f"Core health check timed out on port {port}")
        return 1

    print_success("Health check passed")
    console.print()
    console.print(
        f"[bold green]BaselithCore ready at http://localhost:{port}[/bold green]"
    )
    return 0


def _ensure_runtime_project(root: Path) -> list[Path]:
    """Create missing standalone runtime files without replacing user files."""
    markers = {
        root / "Dockerfile": "BASELITH_CORE_IMAGE",
        root / COMPOSE_FILE: "BASELITH_CORE_IMAGE",
    }
    for path, marker in markers.items():
        if path.is_file() and marker not in path.read_text(encoding="utf-8"):
            raise ValueError(f"Refusing to replace non-Baselith file: {path.name}")

    created: list[Path] = []
    project_name = root.name or "baselithcore-runtime"
    for relative, content in DOCKER_RUNTIME_FILES.items():
        path = root / relative
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = content.replace("{project_name}", project_name).replace(
            "{framework_version}", __version__
        )
        path.write_text(rendered, encoding="utf-8")
        created.append(path)
    return created


def _docker_available() -> bool:
    if DOCKER_BIN is None:
        return False
    result = subprocess.run(
        [DOCKER_BIN, "info"], capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def _compose_environment(image: str) -> dict[str, str]:
    env = os.environ.copy()
    if DOCKER_ENV_FILE.is_file():
        for key, value in dotenv_values(DOCKER_ENV_FILE).items():
            if value is not None and not os.environ.get(key):
                env[key] = value
    env["BASELITH_CORE_IMAGE"] = image
    env["BASELITH_DOCKER_ENV_FILE"] = str(DOCKER_ENV_FILE)
    return env


def _compose(args: list[str], env: dict[str, str]) -> int:
    if DOCKER_BIN is None:
        return 127
    result = subprocess.run(
        [
            DOCKER_BIN,
            "compose",
            "--env-file",
            str(DOCKER_ENV_FILE),
            "-f",
            COMPOSE_FILE,
            *args,
        ],
        env=env,
        check=False,
    )
    return result.returncode


def _wait_for_health(port: str, timeout: int) -> bool:
    if not port.isdecimal() or not 0 < int(port) < 65536:
        return False
    deadline = time.monotonic() + timeout
    url = f"http://localhost:{int(port)}/health"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=5, trust_env=False)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


def register_parser(subparsers, formatter_class):
    """Register the ``up`` command parser."""
    parser = subparsers.add_parser(
        "up",
        help="Download and start the complete Docker runtime",
        description="Prepare and start BaselithCore with its backing services.",
        formatter_class=formatter_class,
    )
    parser.add_argument(
        "--image",
        help=f"Core image to use (default: {DEFAULT_CORE_IMAGE})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to wait for the Core health check (default: 300)",
    )
    return parser


__all__ = ["register_parser", "run_up"]
