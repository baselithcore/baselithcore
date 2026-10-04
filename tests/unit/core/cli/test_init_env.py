"""``baselith init`` writes a ``.env`` a fresh install can start on.

After ``pip install baselith-core`` the operator used to discover by trial
that an undeclared environment is treated as production once auth is
enforced, and that startup then refuses without ``SECRET_KEY``,
``TRUSTED_HOSTS`` and an explicit ``LLM_PROVIDER``. Only ``minimal`` wrote a
``.env`` at all — without any of those, world-readable — and the directory
starters wrote none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.cli.commands.doctor_checks import is_placeholder_secret
from core.cli.commands.env_profiles import (
    DEV_DEFAULTS,
    ensure_dev_env,
    write_project_env,
)
from core.cli.commands.init import PROJECT_TEMPLATES, available_templates, run_init
from core.cli.commands.init_setup import DATA_DIRS, GITIGNORE_ENTRIES

pytestmark = [pytest.mark.unit]


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str) -> Path:
    monkeypatch.chdir(tmp_path)
    assert run_init(project_name="demo", template=template) == 0
    return tmp_path / "demo"


@pytest.mark.parametrize("template", available_templates())
class TestEveryTemplateGetsADevelopmentEnv:
    def test_env_declares_development_and_a_fresh_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str
    ) -> None:
        values = _env_values(_scaffold(tmp_path, monkeypatch, template) / ".env")

        assert values["APP_ENV"] == "development"
        for secret in ("SECRET_KEY", "DB_PASSWORD"):
            assert not is_placeholder_secret(values[secret]), secret
        # SecurityConfig refuses a SECRET_KEY under 32 characters.
        assert len(values["SECRET_KEY"]) >= 32

    def test_env_is_owner_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str
    ) -> None:
        env = _scaffold(tmp_path, monkeypatch, template) / ".env"

        assert env.stat().st_mode & 0o777 == 0o600

    def test_env_is_git_ignored_and_data_dirs_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str
    ) -> None:
        project = _scaffold(tmp_path, monkeypatch, template)

        ignored = (project / ".gitignore").read_text().splitlines()
        for entry in GITIGNORE_ENTRIES:
            assert entry in ignored
        for name in DATA_DIRS:
            assert (project / name).is_dir(), name

    def test_env_matches_the_config_env_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str
    ) -> None:
        """``baselith config env`` has nothing to add to a fresh project."""
        env = _scaffold(tmp_path, monkeypatch, template) / ".env"
        before = env.read_text()

        assert ensure_dev_env(env) == []
        assert env.read_text() == before
        values = _env_values(env)
        for key, value in DEV_DEFAULTS.items():
            assert values[key] == value, key


def test_each_project_gets_its_own_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run_init(project_name="one", template="minimal") == 0
    assert run_init(project_name="two", template="minimal") == 0
    one = _env_values(tmp_path / "one" / ".env")
    two = _env_values(tmp_path / "two" / ".env")

    for secret in ("SECRET_KEY", "DB_PASSWORD"):
        assert one[secret] != two[secret]


def test_generated_env_passes_the_startup_security_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The values satisfy SecurityConfig and are classified as development."""
    from core.config.environment import is_production_env
    from core.config.security import SecurityConfig

    values = _env_values(_scaffold(tmp_path, monkeypatch, "minimal") / ".env")
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    config = SecurityConfig()
    assert not is_production_env()
    assert config.secret_key is not None
    assert config.secret_key.get_secret_value() == values["SECRET_KEY"]
    assert set(config.trusted_hosts) >= {"localhost", "127.0.0.1"}


def test_no_template_ships_its_own_env_file() -> None:
    """A shipped ``.env`` would be the same, public, secret in every project."""
    assert ".env" not in PROJECT_TEMPLATES["minimal"]["files"]
    templates = Path(__file__).resolve().parents[4] / "templates"
    for name in available_templates():
        if name in PROJECT_TEMPLATES:
            continue
        for leaked in (".env", ".env.example"):
            assert not (templates / name / leaked).exists(), f"{name}/{leaked}"


def test_minimal_ships_the_server_baselith_run_serves() -> None:
    files = PROJECT_TEMPLATES["minimal"]["files"]

    assert "from baselith import create_app" in files["backend.py"]
    assert "app = create_app()" in files["backend.py"]
    # Compose interpolates the password generated into .env, never a literal.
    assert "${DB_PASSWORD" in files["docker-compose.yml"]
    assert "127.0.0.1:5432:5432" in files["docker-compose.yml"]


class TestWriteProjectEnv:
    def test_refuses_an_existing_file(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("SECRET_KEY=keep-me-" + "x" * 40 + "\n")

        with pytest.raises(FileExistsError):
            write_project_env(env)
        assert "keep-me" in env.read_text()

    def test_force_replaces_it(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("SECRET_KEY=old\n")
        env.chmod(0o644)

        write_project_env(env, "# header\n", force=True)

        assert env.read_text().startswith("# header\n")
        assert "SECRET_KEY=old" not in env.read_text()
        assert env.stat().st_mode & 0o777 == 0o600

    def test_ignores_a_checkout_env_example(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scaffolding from a checkout must not copy its ``.env.example``."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text("LLM_PROVIDER=openai\nSTRAY=1\n")

        write_project_env(tmp_path / "project.env")

        values = _env_values(tmp_path / "project.env")
        assert "STRAY" not in values
        assert values["LLM_PROVIDER"] == "ollama"


def test_server_starter_shares_the_minimal_server_files() -> None:
    """``baselith-core-template`` carries copies; they must not drift."""
    from core.cli.commands.init_templates import BACKEND_MODULE, SERVICES_COMPOSE

    starter = (
        Path(__file__).resolve().parents[4] / "templates" / "baselith-core-template"
    )
    assert (starter / "backend.py").read_text() == BACKEND_MODULE
    assert (starter / "docker-compose.yml").read_text() == SERVICES_COMPOSE
