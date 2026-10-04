"""Validate built distribution artifacts contain required plugin metadata.

Two directions, because a distribution can be wrong in both:

* every official plugin must arrive with the manifest and README the loader
  and the marketplace read, the sandbox image recipe must arrive with the
  module that builds from it, and
* the in-repo FIXTURE plugins must not arrive at all. ``plugins/example-plugin``
  (the authoring-guide scaffold) and ``plugins/test-project`` (the tree the
  plugin CLI tests scaffold against) were swept into the wheel by
  ``include = ["plugins*"]``, so every install carried a loadable plugin that
  exists only to be asserted on. ``[tool.setuptools.packages.find] exclude``
  keeps them out; this asserts it on the built artifact, because a pattern that
  silently stops matching is how they shipped in the first place.

Member names are not enough, so the wheel is also unpacked and checked the
way an install will use it:

* every shipped plugin that declares ``integrity_sha256`` must verify against
  its INSTALLED copy. A signature covers build files (``pyproject.toml``,
  ``requirements*.txt``) that package discovery does not ship by itself; a
  plugin missing one fails its integrity check on every ``pip install``.
* the Alembic migrations must be locatable inside the package and load into a
  single-head revision graph, with every revision the repository has. Before
  they moved into ``core/db/migrations`` they were not in the wheel at all.
* every ``baselith init`` starter must arrive, file for file, under
  ``core/cli/scaffold_templates``. ``templates/`` is outside every package,
  so a ``pip install`` used to offer only the built-in ``minimal`` template;
  ``build_support/scaffold_templates.py`` copies them in at build time.

Requires PyYAML and Alembic (both runtime dependencies of the package).
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
PLUGIN_FILES = (
    "plugins/api_routers/manifest.yaml",
    "plugins/api_routers/README.md",
    "plugins/browser_agent/manifest.yaml",
    "plugins/browser_agent/README.md",
    "plugins/coding_agent/manifest.yaml",
    "plugins/coding_agent/README.md",
    "plugins/document_sources/manifest.yaml",
    "plugins/document_sources/README.md",
    "plugins/web_scraper/manifest.yaml",
    "plugins/web_scraper/README.md",
)
# The sandbox image recipe. ``core/services/sandbox/docker_factory.py`` builds
# ``agent-sandbox:latest`` from these at first use and FAILS CLOSED when they
# are absent, so a wheel that omits them costs a pip-installed deployment the
# Docker sandbox provider — or, with ``SANDBOX_ALLOW_UNHARDENED_BASE``, swaps
# the reviewed image for one nobody looked at. They are data files inside a
# package: they ship only because ``[tool.setuptools.package-data]`` names
# them, which is the same silently-stops-matching failure as the fixtures
# below, in the other direction. They shipped in neither artifact until the
# entry was added.
SANDBOX_FILES = (
    "core/services/sandbox/Dockerfile.sandbox",
    "core/services/sandbox/requirements.sandbox.txt",
)
# The Alembic environment. ``core.db.migration_config`` resolves it through
# the package; without it no installed deployment can create its schema.
MIGRATIONS_LOCATION = "core/db/migrations"
MIGRATION_FILES = (f"{MIGRATIONS_LOCATION}/env.py",)
#: Every member both artifacts must carry.
REQUIRED_FILES = PLUGIN_FILES + SANDBOX_FILES + MIGRATION_FILES
# Wheel-only: the sdist is the source archive and may legitimately carry the
# fixtures (installing from it re-runs the backend, which applies the same
# `exclude`). The wheel is what `pip install baselith-core` unpacks verbatim.
FORBIDDEN_WHEEL_PREFIXES = (
    "plugins/example-plugin/",
    "plugins/test-project/",
)
# Source maps. The baselithbot dashboard builds with `sourcemap: 'hidden'`,
# which still writes .map files — they are for uploading to an error tracker,
# not for shipping. In the wheel they would roughly triple the packaged
# dashboard and expose its original TypeScript sources, and because `hidden`
# strips the sourceMappingURL comment nothing would ever even request them.
FORBIDDEN_WHEEL_SUFFIXES = (".map",)
# Test files kept beside the code they test (``static/frontend/js/api.test.mjs``
# runs under ``node --test``). Development artifacts, not product.
FORBIDDEN_WHEEL_INFIXES = (".test.", ".spec.")
_MANIFEST_NAMES = ("manifest.yaml", "manifest.yml", "manifest.json")


def _sdist_has_members(path: Path) -> list[str]:
    missing: list[str] = []
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        for required in REQUIRED_FILES:
            if not any(name.endswith(required) for name in names):
                missing.append(required)
    return missing


def _wheel_has_members(path: Path) -> list[str]:
    missing: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for required in REQUIRED_FILES:
            if required not in names:
                missing.append(required)
    return missing


def _preview(names: list[str], limit: int = 5) -> str:
    """Render at most ``limit`` names, noting how many were elided."""
    head = ", ".join(names[:limit])
    if len(names) <= limit:
        return head
    return f"{head} (+{len(names) - limit} more)"


def _wheel_has_fixtures(path: Path) -> list[str]:
    """Return wheel members that belong to an in-repo fixture plugin."""
    with zipfile.ZipFile(path) as archive:
        return [
            name
            for name in archive.namelist()
            if name.startswith(FORBIDDEN_WHEEL_PREFIXES)
        ]


def _wheel_has_source_maps(path: Path) -> list[str]:
    """Return wheel members that are build source maps."""
    with zipfile.ZipFile(path) as archive:
        return [
            name
            for name in archive.namelist()
            if name.endswith(FORBIDDEN_WHEEL_SUFFIXES)
        ]


def _wheel_has_test_files(path: Path) -> list[str]:
    """Return wheel members that are colocated test files."""
    with zipfile.ZipFile(path) as archive:
        return [
            name
            for name in archive.namelist()
            if any(
                infix in name.rsplit("/", 1)[-1] for infix in FORBIDDEN_WHEEL_INFIXES
            )
        ]


@contextlib.contextmanager
def unpacked_wheel(path: Path) -> Iterator[Path]:
    """Unpack ``path`` into a temporary directory removed on exit.

    A wheel is a zip of the installed tree, so its unpacked root is what
    ``pip install`` puts in ``site-packages`` — without resolving a single
    dependency, which keeps the check fast and offline.
    """
    with tempfile.TemporaryDirectory(prefix="wheel-check-") as tmp:
        root = Path(tmp).resolve()
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                # Refuse a member that would land outside the scratch root.
                if not (root / member.filename).resolve().is_relative_to(root):
                    raise ValueError(f"{path.name}: unsafe member {member.filename}")
                archive.extract(member, root)
        yield root


def _declared_hash(manifest: Path) -> str | None:
    import yaml

    text = manifest.read_text(encoding="utf-8")
    data = json.loads(text) if manifest.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        return None
    value = data.get("integrity_sha256")
    return str(value) if value else None


def check_installed_plugins(root: Path, *, label: str) -> list[str]:
    """Verify every signed plugin in an installed tree against its manifest.

    The hasher is the ``core/plugins/integrity.py`` shipped in that same tree,
    loaded by file path, so the check exercises exactly what the installed
    loader will run.

    Args:
        root: The installed (unpacked) tree.
        label: Artifact name used in the messages.

    Returns:
        One message per plugin whose installed copy does not verify.
    """
    integrity_path = root / "core" / "plugins" / "integrity.py"
    spec = importlib.util.spec_from_file_location("_shipped_integrity", integrity_path)
    if spec is None or spec.loader is None or not integrity_path.is_file():
        return [f"{label}: core/plugins/integrity.py is missing"]
    integrity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(integrity)

    violations: list[str] = []
    for plugin_dir in sorted(p for p in (root / "plugins").glob("*") if p.is_dir()):
        manifest = next(
            (plugin_dir / n for n in _MANIFEST_NAMES if (plugin_dir / n).is_file()),
            None,
        )
        declared = _declared_hash(manifest) if manifest is not None else None
        if declared is None:
            continue
        actual = integrity.compute_plugin_hash(plugin_dir)
        if actual.lower() != declared.lower():
            violations.append(
                f"{label}: plugin {plugin_dir.name} fails its integrity check as "
                f"installed (manifest {declared}, installed tree {actual}). A file "
                "its signature covers is not shipped — add it to "
                "[tool.setuptools.package-data] in pyproject.toml — or, if "
                "scripts/check_plugin_integrity.py also reports drift, the plugin "
                "needs re-signing."
            )
    return violations


def _revision_files(location: Path) -> set[str]:
    return {path.name for path in (location / "versions").glob("*.py")}


def check_installed_migrations(
    root: Path, source_root: Path, *, label: str
) -> list[str]:
    """Verify the installed Alembic migrations are complete and loadable.

    Args:
        root: The installed (unpacked) tree.
        source_root: The repository the artifact was built from.
        label: Artifact name used in the messages.

    Returns:
        Messages for a missing environment, revisions the install lacks, or a
        revision graph that does not load into exactly one head.
    """
    location = root / MIGRATIONS_LOCATION
    if not (location / "env.py").is_file():
        return [
            f"{label}: {MIGRATIONS_LOCATION} is not shipped — restore its "
            "`core.db` entry under [tool.setuptools.package-data]."
        ]
    violations = [
        f"{label}: missing migration {MIGRATIONS_LOCATION}/versions/{name}"
        for name in sorted(
            _revision_files(source_root / MIGRATIONS_LOCATION)
            - _revision_files(location)
        )
    ]

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(location).replace("%", "%%"))
    try:
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception as exc:  # any load failure is the finding itself
        return [*violations, f"{label}: migrations fail to load: {exc}"]
    if len(heads) != 1:
        violations.append(
            f"{label}: migrations resolve to {len(heads)} head(s) "
            f"({', '.join(heads) or 'none'}), expected exactly one."
        )
    return violations


def _scaffold_hook() -> tuple[tuple[str, ...], str]:
    """The starter list and target the build hook uses, loaded by path."""
    path = REPO_ROOT / "build_support" / "scaffold_templates.py"
    spec = importlib.util.spec_from_file_location("_scaffold_hook", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.SCAFFOLD_TEMPLATES), str(module.PACKAGE_TARGET)


def check_installed_scaffold_templates(
    root: Path,
    source_root: Path,
    names: tuple[str, ...],
    *,
    label: str,
    target: str = "core/cli/scaffold_templates",
) -> list[str]:
    """Verify every ``baselith init`` starter is installed file for file.

    Args:
        root: The installed (unpacked) tree.
        source_root: The repository the artifact was built from.
        names: The starter directories the wheel must carry.
        label: Artifact name used in the messages.
        target: Where the starters sit inside the installed tree.

    Returns:
        One message per missing starter or missing file.
    """
    violations: list[str] = []
    for name in names:
        installed = root / target / name
        if not installed.is_dir():
            violations.append(
                f"{label}: scaffold template {name} is not shipped under "
                f"{target} — check the build_py cmdclass in pyproject.toml "
                "and MANIFEST.in."
            )
            continue
        source = source_root / "templates" / name
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(source)
            if not path.is_file() or "__pycache__" in rel.parts:
                continue
            if path.name == ".DS_Store" or path.suffix in (".pyc", ".pyo"):
                continue
            if not (installed / rel).is_file():
                violations.append(
                    f"{label}: scaffold template file missing: "
                    f"{target}/{name}/{rel.as_posix()}"
                )
    return violations


def main() -> int:
    """CLI entrypoint."""
    wheel_files = sorted(DIST_DIR.glob("*.whl"))
    sdist_files = sorted(DIST_DIR.glob("*.tar.gz"))

    if not wheel_files or not sdist_files:
        print("Both wheel and sdist artifacts must exist in dist/.", file=sys.stderr)
        return 1

    violations: list[str] = []

    for wheel in wheel_files:
        missing = _wheel_has_members(wheel)
        if missing:
            violations.append(f"{wheel.name}: missing {', '.join(missing)}")
        shipped_fixtures = _wheel_has_fixtures(wheel)
        if shipped_fixtures:
            violations.append(
                f"{wheel.name}: ships in-repo fixture plugins: "
                f"{_preview(shipped_fixtures)}. "
                "Restore the `exclude` patterns under "
                "[tool.setuptools.packages.find] in pyproject.toml. If those "
                "patterns are present and this still fails, the build reused "
                "stale local artifacts: `build/` keeps files that are no longer "
                "part of any package, and a stale `*.egg-info/SOURCES.txt` "
                "re-injects them because pyproject-based setuptools defaults "
                "include-package-data to true. Delete both and rebuild "
                "(CI checks out fresh, so it never sees this)."
            )
        source_maps = _wheel_has_source_maps(wheel)
        if source_maps:
            violations.append(
                f"{wheel.name}: ships source maps: {_preview(source_maps)}. "
                "The UI builds with sourcemap: 'hidden', so nothing requests "
                "them — they exist to be uploaded to an error tracker, not "
                "shipped. Restore the `ui/dist/**/*.map` entry under "
                "[tool.setuptools.exclude-package-data] in pyproject.toml."
            )

        test_files = _wheel_has_test_files(wheel)
        if test_files:
            violations.append(
                f"{wheel.name}: ships test files: {_preview(test_files)}. Restore "
                "the `**/*.test.*` / `**/*.spec.*` entries under "
                "[tool.setuptools.exclude-package-data] in pyproject.toml."
            )
        with unpacked_wheel(wheel) as installed:
            violations.extend(check_installed_plugins(installed, label=wheel.name))
            violations.extend(
                check_installed_migrations(installed, REPO_ROOT, label=wheel.name)
            )
            names, target = _scaffold_hook()
            violations.extend(
                check_installed_scaffold_templates(
                    installed, REPO_ROOT, names, label=wheel.name, target=target
                )
            )

    for sdist in sdist_files:
        missing = _sdist_has_members(sdist)
        if missing:
            violations.append(f"{sdist.name}: missing {', '.join(missing)}")

    if violations:
        print("Distribution artifact validation failed:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print("Distribution artifacts OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
