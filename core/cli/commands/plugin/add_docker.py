"""Docker runtime integration for Git-installed plugins."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from packaging.requirements import InvalidRequirement, Requirement

from core.cli.ui import print_error, print_info, print_step, print_success

PLUGIN_REQUIREMENTS = Path("configs") / "plugin-requirements.txt"
COMPOSE_FILE = "docker-compose.core.yml"
DOCKER_ENV_FILE = Path("configs") / ".env.docker.core"
COMPOSE_SHELL_OVERRIDE_KEYS = {
    "BASELITH_HTTP_PORT",
    "BASELITH_POSTGRES_PORT",
    "BASELITH_REDIS_PORT",
    "BASELITH_QDRANT_PORT",
    "COMPOSE_PROJECT_NAME",
}


def install_plugin_into_docker(plugin_name: str, manifest: dict[str, Any]) -> int:
    """Build frontend, rebuild the API image, restart it, and probe health."""
    _write_plugin_requirements()
    if _build_frontend(plugin_name, manifest) != 0:
        return 1
    if _compose(["build", "api"]) != 0:
        return 1
    if _compose(["up", "-d", "api"]) != 0:
        return 1
    if not _wait_for_http("/health", 200):
        print_error("Docker API did not become healthy after plugin install.")
        return 1
    if not _probe_plugin(plugin_name, manifest):
        return 1
    print_success("Plugin loaded")
    print_success("Health check passed")
    print_success("Plugin ready")
    return 0


def _build_frontend(plugin_name: str, manifest: dict[str, Any]) -> int:
    frontend = _frontend_config(plugin_name, manifest)
    if frontend is None:
        print_info("No frontend declared or detected.")
        return 0

    workdir = Path(frontend["path"])
    output = Path(frontend["output"])
    package_manager = frontend["package_manager"]
    build_command = frontend["build_command"]

    print_step(f"Building frontend for {plugin_name} with Docker Node...")
    for local_dependency in _local_file_dependency_paths(workdir):
        dependency_manager = _detect_package_manager(local_dependency)
        result = subprocess.run(
            _node_install_command(local_dependency, dependency_manager),
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print_error(
                f"Frontend dependency install failed: {local_dependency}"
            )
            return result.returncode
    command = _node_command(workdir, package_manager, build_command)
    result = subprocess.run(command, text=True, check=False)
    if result.returncode != 0:
        print_error(f"Frontend build failed for plugin '{plugin_name}'.")
        return result.returncode
    if not output.exists():
        print_error(f"Frontend build output not found: {output}")
        return 1
    print_success("Frontend built")
    return 0


def _frontend_config(
    plugin_name: str, manifest: dict[str, Any]
) -> dict[str, str] | None:
    raw = manifest.get("frontend")
    if isinstance(raw, dict):
        path = Path(raw.get("path") or raw.get("directory") or "ui")
        if not path.is_absolute():
            path = Path("plugins") / plugin_name / path
        package_manager = str(
            raw.get("package_manager") or _detect_package_manager(path)
        )
        build_command = str(
            raw.get("build_command") or _default_build_command(package_manager)
        )
        output = Path(
            raw.get("output_dir") or raw.get("output") or _detect_output_dir(path)
        )
        if not output.is_absolute():
            output = path / output
        return {
            "path": str(path),
            "package_manager": package_manager,
            "build_command": build_command,
            "output": str(output),
        }

    path = _detect_frontend_path(plugin_name)
    if path is None:
        return None
    package_manager = _detect_package_manager(path)
    return {
        "path": str(path),
        "package_manager": package_manager,
        "build_command": _default_build_command(package_manager),
        "output": str(path / _detect_output_dir(path)),
    }


def _detect_package_manager(path: Path) -> str:
    if (path / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (path / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _detect_frontend_path(plugin_name: str) -> Path | None:
    plugin_dir = Path("plugins") / plugin_name
    for directory in ("ui", "frontend"):
        path = plugin_dir / directory
        if (path / "package.json").is_file():
            return path
    return None


def _default_build_command(package_manager: str) -> str:
    if package_manager == "pnpm":
        return "pnpm build"
    if package_manager == "yarn":
        return "yarn build"
    return "npm run build"


def _detect_output_dir(path: Path) -> str:
    next_config = path / "next.config.js"
    if next_config.is_file() and "output: 'export'" in next_config.read_text(
        encoding="utf-8"
    ):
        return "out"
    return "dist"


def _node_command(path: Path, package_manager: str, build_command: str) -> list[str]:
    install = {
        "pnpm": "npx --yes pnpm install --frozen-lockfile",
        "yarn": "npx --yes yarn install --frozen-lockfile",
        "npm": "npm ci",
    }.get(package_manager, "npm ci")
    if package_manager in {"pnpm", "yarn"}:
        build_command = f"npx --yes {build_command}"
    shell = f"{install} && {build_command}"
    return _node_run_command(path, shell)


def _node_install_command(path: Path, package_manager: str) -> list[str]:
    install = {
        "pnpm": "npx --yes pnpm install --frozen-lockfile",
        "yarn": "npx --yes yarn install --frozen-lockfile",
        "npm": "npm ci",
    }.get(package_manager, "npm ci")
    return _node_run_command(path, install)


def _node_run_command(path: Path, shell: str) -> list[str]:
    shell = f'export PATH="$PWD/node_modules/.bin:$PATH"; {shell}'
    volume_path = path.resolve()
    workdir = "/work"
    volume_target = "/work"
    try:
        plugin_relative_path = path.resolve().relative_to(Path("plugins").resolve())
    except ValueError:
        pass
    else:
        volume_path = Path("plugins").resolve()
        volume_target = "/plugins"
        workdir = f"/plugins/{plugin_relative_path.as_posix()}"
    return [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{volume_path}:{volume_target}",
        "-w",
        workdir,
        "node:22-bookworm-slim",
        "sh",
        "-lc",
        shell,
    ]


def _local_file_dependency_paths(path: Path) -> list[Path]:
    package_json = path / "package.json"
    if not package_json.is_file():
        return []
    try:
        package_data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    local_paths: list[Path] = []
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        dependencies = package_data.get(section)
        if not isinstance(dependencies, dict):
            continue
        for value in dependencies.values():
            if not isinstance(value, str) or not value.startswith("file:"):
                continue
            dependency_path = (path / value.removeprefix("file:")).resolve()
            if not (dependency_path / "package.json").is_file():
                continue
            if dependency_path not in local_paths:
                local_paths.append(dependency_path)
    return local_paths


def _write_plugin_requirements() -> None:
    requirements: list[str] = []
    for manifest_path in sorted(Path("plugins").glob("*/manifest.*")):
        manifest = _load_manifest(manifest_path)
        if not manifest:
            continue
        if not _plugin_enabled(str(manifest.get("name") or manifest_path.parent.name)):
            continue
        for dep in manifest.get("python_dependencies", []) or []:
            if isinstance(dep, str) and dep not in requirements:
                requirements.append(dep)

    PLUGIN_REQUIREMENTS.parent.mkdir(parents=True, exist_ok=True)
    PLUGIN_REQUIREMENTS.write_text(
        "# Generated by: baselith plugin add --docker\n"
        + "\n".join(requirements)
        + "\n",
        encoding="utf-8",
    )
    print_success("Python dependencies ready")


def _legacy_dependency_hints(plugin_dir: Path) -> list[str]:
    """Best-effort bridge for legacy plugins missing manifest dependencies."""
    hints: list[str] = []
    pattern = re.compile(r"pip install\s+([^\n\r]+)")
    for path in plugin_dir.rglob("*.py"):
        if any(part in {"tests", "ui", "node_modules"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in pattern.finditer(text):
            for package in _legacy_pip_packages(match.group(1)):
                if package not in hints:
                    hints.append(package)
    return hints


def _legacy_pip_packages(arguments: str) -> list[str]:
    packages: list[str] = []
    for token in arguments.split():
        token = token.strip("'\"`,);.")
        if token in {"&&", "|", "||"}:
            break
        if token.startswith("-"):
            continue
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*", token):
            continue
        if token in {".", "./", "../"}:
            continue
        if "/" in token or "\\" in token:
            continue
        try:
            Requirement(token)
        except InvalidRequirement:
            continue
        packages.append(token)
    return packages


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        if path.suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _plugin_enabled(plugin_name: str) -> bool:
    config = Path("configs") / "plugins.yaml"
    if not config.is_file():
        return False
    try:
        import yaml

        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        entry = data.get(plugin_name)
        return bool(entry.get("enabled")) if isinstance(entry, dict) else bool(entry)
    except Exception:
        return False


def _compose(args: list[str]) -> int:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(DOCKER_ENV_FILE),
        "-f",
        COMPOSE_FILE,
        *args,
    ]
    env = _compose_environment(DOCKER_ENV_FILE)
    env["BASELITH_DOCKER_ENV_FILE"] = str(DOCKER_ENV_FILE)
    result = subprocess.run(command, env=env, check=False)
    return result.returncode


def _compose_environment(env_file: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in _read_env_file(env_file).items():
        if key in COMPOSE_SHELL_OVERRIDE_KEYS and os.environ.get(key):
            continue
        env[key] = value
    return env


def _read_env_file(path: Path) -> dict[str, str]:
    """Read a dotenv-style file for Docker Compose interpolation."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _wait_for_http(path: str, expected: int, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    port = os.environ.get("BASELITH_HTTP_PORT") or "8000"
    url = f"http://localhost:{port}{path}"
    while time.time() < deadline:
        try:
            request = Request(url, method="GET")
            with urlopen(request, timeout=5) as response:
                if response.status == expected:
                    return True
        except Exception:
            time.sleep(2)
    return False


def _probe_plugin(plugin_name: str, manifest: dict[str, Any]) -> bool:
    health = manifest.get("health_endpoint")
    if isinstance(health, str) and health:
        return _wait_for_http(health, 200, timeout=45)

    port = os.environ.get("BASELITH_HTTP_PORT") or "8000"
    url = f"http://localhost:{port}/{plugin_name}/"
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=10) as response:
            if 200 <= response.status < 400:
                return True
    except Exception as exc:
        print_error(f"Plugin probe failed at /{plugin_name}/", str(exc))
        return False
    return False


__all__ = ["install_plugin_into_docker"]
