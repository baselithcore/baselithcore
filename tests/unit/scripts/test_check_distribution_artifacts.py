"""The distribution gate checks what an INSTALL gets, not only member names.

Two defects shipped because the gate only matched file names: a signed plugin
whose signature covers files the wheel did not carry (it failed its integrity
check on every pip install), and Alembic migrations that were not in the wheel
at all. Both checks now run against the unpacked wheel.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

import pytest

from scripts.check_distribution_artifacts import (
    check_installed_migrations,
    check_installed_plugins,
    check_installed_scaffold_templates,
    unpacked_wheel,
)

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[3]

_REVISION = '''"""{rev}."""

revision = "{rev}"
down_revision = {down}
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def _install_integrity(root: Path) -> None:
    target = root / "core" / "plugins"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / "core" / "plugins" / "integrity.py", target)


def _signed_plugin(root: Path, name: str, files: dict[str, str]) -> str:
    """Write a plugin under ``root/plugins`` and sign it; return the digest."""
    import importlib.util

    plugin = root / "plugins" / name
    plugin.mkdir(parents=True)
    for rel, text in files.items():
        (plugin / rel).write_text(text, encoding="utf-8")
    (plugin / "manifest.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "_test_integrity", REPO_ROOT / "core" / "plugins" / "integrity.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    digest: str = module.compute_plugin_hash(plugin)
    (plugin / "manifest.yaml").write_text(
        f"name: {name}\nintegrity_sha256: {digest}\n", encoding="utf-8"
    )
    return digest


class TestInstalledPlugins:
    def test_complete_plugin_passes(self, tmp_path: Path) -> None:
        _install_integrity(tmp_path)
        _signed_plugin(
            tmp_path, "demo", {"plugin.py": "X = 1\n", "requirements.txt": "a\n"}
        )

        assert check_installed_plugins(tmp_path, label="w.whl") == []

    def test_signed_file_missing_from_install_fails(self, tmp_path: Path) -> None:
        _install_integrity(tmp_path)
        _signed_plugin(
            tmp_path, "demo", {"plugin.py": "X = 1\n", "requirements.txt": "a\n"}
        )
        (tmp_path / "plugins" / "demo" / "requirements.txt").unlink()

        (violation,) = check_installed_plugins(tmp_path, label="w.whl")

        assert "demo" in violation
        assert "package-data" in violation

    def test_unsigned_plugin_is_not_checked(self, tmp_path: Path) -> None:
        _install_integrity(tmp_path)
        plugin = tmp_path / "plugins" / "loose"
        plugin.mkdir(parents=True)
        (plugin / "manifest.yaml").write_text("name: loose\n", encoding="utf-8")

        assert check_installed_plugins(tmp_path, label="w.whl") == []


def _migrations(root: Path, revisions: dict[str, str | None]) -> None:
    location = root / "core" / "db" / "migrations"
    (location / "versions").mkdir(parents=True)
    (location / "env.py").write_text("", encoding="utf-8")
    for rev, down in revisions.items():
        (location / "versions" / f"{rev}.py").write_text(
            _REVISION.format(rev=rev, down=repr(down)), encoding="utf-8"
        )


class TestInstalledMigrations:
    def test_linear_history_passes(self, tmp_path: Path) -> None:
        installed, source = tmp_path / "installed", tmp_path / "source"
        _migrations(installed, {"r1": None, "r2": "r1"})
        _migrations(source, {"r1": None, "r2": "r1"})

        assert check_installed_migrations(installed, source, label="w.whl") == []

    def test_missing_directory_fails(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        _migrations(source, {"r1": None})

        (violation,) = check_installed_migrations(
            tmp_path / "installed", source, label="w.whl"
        )

        assert "core/db/migrations" in violation

    def test_revision_missing_from_install_fails(self, tmp_path: Path) -> None:
        installed, source = tmp_path / "installed", tmp_path / "source"
        _migrations(installed, {"r1": None})
        _migrations(source, {"r1": None, "r2": "r1"})

        violations = check_installed_migrations(installed, source, label="w.whl")

        assert any("r2.py" in v for v in violations)

    def test_multiple_heads_fail(self, tmp_path: Path) -> None:
        installed, source = tmp_path / "installed", tmp_path / "source"
        _migrations(installed, {"r1": None, "a": "r1", "b": "r1"})
        _migrations(source, {"r1": None, "a": "r1", "b": "r1"})

        (violation,) = check_installed_migrations(installed, source, label="w.whl")

        assert "head" in violation


class TestInstalledScaffoldTemplates:
    @staticmethod
    def _source(root: Path) -> Path:
        source = root / "repo"
        (source / "templates" / "rag-system").mkdir(parents=True)
        (source / "templates" / "rag-system" / "README.md").write_text("r")
        (source / "templates" / "rag-system" / "main.py").write_text("m")
        return source

    def test_complete_install_passes(self, tmp_path: Path) -> None:
        source = self._source(tmp_path)
        shutil.copytree(
            source / "templates",
            tmp_path / "site" / "core" / "cli" / "scaffold_templates",
        )

        assert (
            check_installed_scaffold_templates(
                tmp_path / "site", source, ("rag-system",), label="w.whl"
            )
            == []
        )

    def test_missing_template_fails(self, tmp_path: Path) -> None:
        source = self._source(tmp_path)
        (tmp_path / "site").mkdir()

        found = check_installed_scaffold_templates(
            tmp_path / "site", source, ("rag-system",), label="w.whl"
        )
        assert len(found) == 1
        assert "rag-system" in found[0]

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        source = self._source(tmp_path)
        installed = tmp_path / "site" / "core" / "cli" / "scaffold_templates"
        (installed / "rag-system").mkdir(parents=True)
        (installed / "rag-system" / "README.md").write_text("r")

        found = check_installed_scaffold_templates(
            tmp_path / "site", source, ("rag-system",), label="w.whl"
        )
        assert found == [
            "w.whl: scaffold template file missing: "
            "core/cli/scaffold_templates/rag-system/main.py"
        ]


def test_unpacked_wheel_is_removed_afterwards(tmp_path: Path) -> None:
    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("core/x.py", "X = 1\n")

    with unpacked_wheel(wheel) as root:
        seen = root
        content = (root / "core" / "x.py").read_bytes()

    assert hashlib.sha256(content).hexdigest() == hashlib.sha256(b"X = 1\n").hexdigest()
    assert not seen.exists()


def test_member_escaping_the_scratch_root_is_refused(tmp_path: Path) -> None:
    wheel = tmp_path / "evil-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("../escape.py", "X = 1\n")

    with pytest.raises(ValueError, match="unsafe member"), unpacked_wheel(wheel):
        pass
