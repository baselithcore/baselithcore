"""Docker runtime integration for Git-installed plugins."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import dotenv_values
from packaging.requirements import InvalidRequirement, Requirement

from core.cli.ui import print_error, print_info, print_step, print_success

PLUGIN_REQUIREMENTS = Path("configs") / "plugin-requirements.txt"
COMPOSE_FILE = "docker-compose.core.yml"
DOCKER_ENV_FILE = Path("configs") / ".env.docker.core"
COMPOSE_SHELL_OVERRIDE_KEYS = {
    "BASELITH_CORE_IMAGE",
    "BASELITH_HTTP_PORT",
    "BASELITH_POSTGRES_PORT",
    "BASELITH_REDIS_PORT",
    "BASELITH_QDRANT_PORT",
    "COMPOSE_PROJECT_NAME",
}


def install_plugin_into_docker(plugin_name: str, manifest: dict[str, Any]) -> int:
    """Build frontend, rebuild the API image, restart it, and probe health."""
    try:
        _write_plugin_requirements()
    except (OSError, ValueError) as exc:
        print_error("Cannot prepare plugin requirements", str(exc))
        return 1
    if _build_frontends(plugin_name, manifest) != 0:
        return 1
    if _compose(["build", "api"]) != 0:
        return 1
    print_success("Python dependencies installed in Docker image")
    if _compose(["up", "-d", "api"]) != 0:
        return 1
    if not _wait_for_http("/health", 200):
        print_error("Docker API did not become healthy after plugin install.")
        return 1
    if not _probe_plugin(plugin_name, manifest):
        return 1
    print_success("Plugin HTTP endpoint reachable")
    print_success("Health check passed")
    print_success("Plugin ready")
    return 0


def _build_frontends(plugin_name: str, manifest: dict[str, Any]) -> int:
    """Build dependency frontends first, once each, rejecting cycles."""
    from core.plugins.version import check_plugin_dependency

    from .add import _check_core_compatibility
    from .add import _load_manifest as load_manifest
    from .install_validation import validate_install_manifest

    built: set[str] = set()
    visiting: set[str] = set()

    def build(name: str, data: dict[str, Any]) -> int:
        if validate_install_manifest(data):
            print_error(f"Invalid installation manifest: {name}")
            return 1
        if name in visiting:
            print_error(f"Cyclic plugin dependency: {name}")
            return 1
        if name in built:
            return 0
        visiting.add(name)
        dependencies = data.get("plugin_dependencies") or {}
        for dependency in dependencies:
            dep_manifest = load_manifest(Path("plugins") / dependency)
            if not dep_manifest or not _check_core_compatibility(dep_manifest):
                print_error(f"Invalid plugin dependency: {dependency}")
                return 1
            constraint = (
                dependencies[dependency] if isinstance(dependencies, dict) else None
            )
            if constraint and not check_plugin_dependency(
                str(dep_manifest.get("version", "")), constraint
            ):
                print_error(f"Plugin dependency version mismatch: {dependency}")
                return 1
            if build(dependency, dep_manifest) != 0:
                return 1
        # Legacy dependencies may ship prebuilt assets outside ui/dist. Only
        # rebuild their frontend when its build contract is explicitly declared.
        if name == plugin_name or isinstance(data.get("frontend"), dict):
            if _build_frontend(name, data) != 0:
                return 1
        visiting.remove(name)
        built.add(name)
        return 0

    return build(plugin_name, manifest)


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
            print_error(f"Frontend dependency install failed: {local_dependency}")
            return result.returncode
    command = _node_command(workdir, package_manager, build_command)
    result = subprocess.run(command, text=True, check=False)
    if result.returncode != 0:
        print_error(f"Frontend build failed for plugin '{plugin_name}'.")
        return result.returncode
    if not output.is_dir() or not any(p.is_file() for p in output.rglob("*")):
        print_error(f"Frontend build output missing or empty: {output}")
        return 1
    print_success("Frontend built")
    return 0


def _frontend_config(
    plugin_name: str, manifest: dict[str, Any]
) -> dict[str, str] | None:
    raw = manifest.get("frontend")
    if raw is False:
        return None
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

    detected_path = _detect_frontend_path(plugin_name)
    if detected_path is None:
        return None
    package_manager = _detect_package_manager(detected_path)
    return {
        "path": str(detected_path),
        "package_manager": package_manager,
        "build_command": _default_build_command(package_manager),
        "output": str(detected_path / _detect_output_dir(detected_path)),
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
    for plugin_dir in sorted(Path("plugins").iterdir()):
        if not plugin_dir.is_dir() or not _plugin_enabled(plugin_dir.name):
            continue
        manifest_path = next(
            (
                plugin_dir / f"manifest{ext}"
                for ext in (".yaml", ".yml", ".json")
                if (plugin_dir / f"manifest{ext}").is_file()
            ),
            None,
        )
        if manifest_path is None:
            raise ValueError(f"Enabled plugin '{plugin_dir.name}' has no manifest")
        manifest = _load_manifest(manifest_path)
        if not manifest:
            raise ValueError(f"Invalid manifest: {manifest_path}")
        dependencies = manifest.get("python_dependencies", []) or []
        if not isinstance(dependencies, list):
            raise ValueError(f"python_dependencies must be a list: {manifest_path}")
        for dep in dependencies:
            if not isinstance(dep, str) or "\n" in dep or "\r" in dep:
                raise ValueError(f"Invalid Python requirement in {manifest_path}")
            try:
                Requirement(dep)
            except InvalidRequirement as exc:
                raise ValueError(
                    f"Invalid Python requirement in {manifest_path}"
                ) from exc
            if dep not in requirements:
                requirements.append(dep)

    PLUGIN_REQUIREMENTS.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Generated by: baselith plugin add --docker\n"
        + "\n".join(sorted(requirements))
        + "\n"
    )
    if (
        not PLUGIN_REQUIREMENTS.exists()
        or PLUGIN_REQUIREMENTS.read_text(encoding="utf-8") != content
    ):
        temporary = PLUGIN_REQUIREMENTS.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(PLUGIN_REQUIREMENTS)
    print_info("Python requirements prepared; installation runs during Docker build.")


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, yaml.YAMLError):
        return None


def _plugin_enabled(plugin_name: str) -> bool:
    config = Path("configs") / "plugins.yaml"
    if not config.is_file():
        return False
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("configs/plugins.yaml must contain a mapping")
        entry = data.get(plugin_name)
        return bool(entry.get("enabled")) if isinstance(entry, dict) else bool(entry)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("Cannot read configs/plugins.yaml") from exc


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
    return {
        key: value for key, value in dotenv_values(path).items() if value is not None
    }


def _wait_for_http(path: str, expected: int, timeout: int = 90) -> bool:
    deadline = time.monotonic() + timeout
    port = _compose_environment(DOCKER_ENV_FILE).get("BASELITH_HTTP_PORT") or "8000"
    if not port.isdecimal() or not 0 < int(port) < 65536:
        print_error("BASELITH_HTTP_PORT must be between 1 and 65535")
        return False
    if not path.startswith("/") or path.startswith("//"):
        print_error("Health endpoint must be a local HTTP path")
        return False
    url = f"http://localhost:{int(port)}{path}"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                url, timeout=5, follow_redirects=False, trust_env=False
            )
            if response.status_code == expected:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    print_error(f"HTTP check timed out: {path} on port {port} (expected {expected})")
    return False


def _probe_plugin(plugin_name: str, manifest: dict[str, Any]) -> bool:
    health = manifest.get("health_endpoint")
    if isinstance(health, str) and health:
        return _wait_for_http(health, 200, timeout=45)

    return _wait_for_http(f"/{plugin_name}/", 200, timeout=45)


__all__ = ["install_plugin_into_docker"]
