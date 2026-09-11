"""Install local plugins directly from Git repositories."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from core.cli.ui import (
    console,
    print_error,
    print_info,
    print_step,
    print_success,
    print_warning,
)

from .deps import deps_check, deps_install
from .local_manage import enable_local_plugin
from .local_validate import validate_local_plugin


def add_plugin(
    source: str,
    name: str | None = None,
    ref: str | None = None,
    force: bool = False,
    install_deps: bool = False,
    docker: bool = False,
) -> int:
    """Clone, validate, enable, and optionally install deps for a Git plugin."""
    plugin_name = _resolve_name(source, name)
    plugin_dir = Path("plugins") / plugin_name

    if plugin_dir.exists():
        if force:
            if not _remove_existing(plugin_dir):
                return 1
        elif plugin_dir.is_dir():
            print_info(f"Plugin '{plugin_name}' already exists; reusing it.")
        else:
            print_error(
                f"Plugin '{plugin_name}' already exists.",
                "Use --force to replace it, or pass --name for another folder.",
            )
            return 1
    if not plugin_dir.exists() and not _clone(source, plugin_dir, ref):
        return 1

    manifest = _load_manifest(plugin_dir)
    if not _validate_install_shape(plugin_dir, manifest):
        print_error(f"Plugin '{plugin_name}' was cloned but is not installable.")
        return 1
    print_success("Manifest valid")
    if not _check_core_compatibility(manifest):
        return 1
    print_success("Core compatible")

    _copy_env_example(plugin_dir)

    if not _install_plugin_dependencies(manifest):
        return 1

    enable_code = enable_local_plugin(plugin_name)
    if enable_code != 0:
        return enable_code

    validate_code = validate_local_plugin(plugin_name)
    deps_code = deps_check(plugin_name)
    if deps_code != 0 and install_deps and not docker:
        deps_code = deps_install(plugin_name, yes=True)
    elif deps_code != 0 and not docker:
        print_info(
            "Install missing Python deps with: "
            f"baselith plugin deps install {plugin_name} -y"
        )

    if docker:
        from .add_docker import install_plugin_into_docker

        docker_code = install_plugin_into_docker(plugin_name, manifest or {})
        return docker_code

    if validate_code != 0 or deps_code != 0:
        print_warning(
            "Plugin was added, but some readiness checks need attention before "
            "all features are available."
        )

    _print_next_steps(plugin_name)
    return 0


def _resolve_name(source: str, explicit_name: str | None) -> str:
    if explicit_name:
        return _slugify(explicit_name)

    path = urlparse(source).path if "://" in source else source
    stem = Path(path.rstrip("/")).name
    if stem.endswith(".git"):
        stem = stem[:-4]
    if stem.startswith("plugin-"):
        stem = stem[len("plugin-") :]
    return _slugify(stem)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return slug or "plugin"


def _clone(source: str, plugin_dir: Path, ref: str | None) -> bool:
    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone"]
    if ref:
        command.extend(["--branch", ref])
    command.extend([source, str(plugin_dir)])

    print_step(f"Cloning plugin into {plugin_dir}...")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print_error("git clone failed", result.stderr.strip())
        return False
    print_success("Repository cloned")
    return True


def _copy_env_example(plugin_dir: Path) -> None:
    env_example = plugin_dir / ".env.example"
    env_file = plugin_dir / ".env"
    if not env_example.is_file() or env_file.exists():
        return
    shutil.copyfile(env_example, env_file)
    print_info(f"Created {env_file} from .env.example")


def _validate_install_shape(plugin_dir: Path, manifest: dict | None) -> bool:
    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.is_file():
        print_error(f"Missing plugin.py in {plugin_dir}")
        return False

    try:
        tree = ast.parse(plugin_file.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        print_error(f"Python syntax error in {plugin_file}", str(exc))
        return False

    valid_bases = {"Plugin", "AgentPlugin", "RouterPlugin", "GraphPlugin"}
    has_plugin_class = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {
            base.id
            for base in node.bases
            if isinstance(base, ast.Name)
        }
        bases.update(
            base.attr
            for base in node.bases
            if isinstance(base, ast.Attribute)
        )
        if bases.intersection(valid_bases):
            has_plugin_class = True
            break
    if not has_plugin_class:
        print_error(f"No Baselith plugin class found in {plugin_file}")
        return False

    if manifest is None:
        print_error(f"Missing or unreadable manifest in {plugin_dir}")
        return False
    missing = [
        field
        for field in ("name", "version", "description")
        if not manifest.get(field)
    ]
    if missing:
        print_error("Plugin manifest is incomplete", ", ".join(missing))
        return False
    return True


def _install_plugin_dependencies(manifest: dict | None) -> bool:
    if not manifest:
        return True
    deps = manifest.get("plugin_dependencies", {}) or {}
    names = deps.keys() if isinstance(deps, dict) else deps
    for dep_name in names:
        if not isinstance(dep_name, str):
            continue
        if (Path("plugins") / dep_name).is_dir():
            if enable_local_plugin(dep_name) != 0:
                print_error(f"Could not enable plugin dependency '{dep_name}'.")
                return False
            continue
        source = f"https://github.com/baselithcore/plugin-{dep_name}"
        print_info(f"Installing plugin dependency '{dep_name}' from {source}")
        code = add_plugin(source, name=dep_name)
        if code != 0:
            print_error(f"Could not install plugin dependency '{dep_name}'.")
            return False
    return True


def _check_core_compatibility(manifest: dict | None) -> bool:
    if not manifest:
        return False
    min_core = manifest.get("min_core_version")
    if not min_core:
        print_warning("Manifest does not declare min_core_version.")
        return True
    try:
        from packaging.version import Version

        from core._version import __version__

        if Version(__version__) < Version(str(min_core)):
            print_error(
                "Plugin requires a newer Baselith core.",
                f"Installed {__version__}, required >= {min_core}",
            )
            return False
    except Exception as exc:
        print_warning(f"Could not verify core compatibility: {exc}")
    return True


def _load_manifest(plugin_dir: Path) -> dict | None:
    for suffix in (".yaml", ".yml", ".json"):
        manifest_path = plugin_dir / f"manifest{suffix}"
        if not manifest_path.is_file():
            continue
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                if suffix == ".json":
                    data = json.load(handle)
                else:
                    import yaml

                    data = yaml.safe_load(handle)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            print_warning(f"Could not read {manifest_path}: {exc}")
            return None
    return None


def _remove_existing(plugin_dir: Path) -> bool:
    if not plugin_dir.is_dir():
        print_error(f"Cannot replace non-directory path: {plugin_dir}")
        return False

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=plugin_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        print_error(
            f"Refusing to replace dirty plugin '{plugin_dir.name}'.",
            "Commit, stash, or delete it manually first.",
        )
        return False

    shutil.rmtree(plugin_dir)
    return True


def _print_next_steps(plugin_name: str) -> None:
    console.print()
    print_success(f"Plugin '{plugin_name}' is ready for the next API restart.")
    console.print(f"[dim]Plugin env file: plugins/{plugin_name}/.env[/dim]")
    console.print("[bold]Next command:[/bold]")
    console.print(
        "BASELITH_DOCKER_ENV_FILE=configs/.env.docker.core "
        "docker compose --env-file configs/.env.docker.core "
        "-f docker-compose.core.yml restart api"
    )
