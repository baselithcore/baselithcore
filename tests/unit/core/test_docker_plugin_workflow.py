from pathlib import Path
from unittest.mock import patch

from core.cli.commands.doctor_checks import is_placeholder_secret
from core.cli.commands.env_profiles import (
    ensure_dev_env,
    ensure_docker_core_env,
    set_docker_core_image,
)
from core.cli.commands.plugin import add_docker


def test_setup_replaces_long_secret_key_placeholder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text(
        "SECRET_KEY=__CHANGE_ME_GENERATE_WITH_openssl_rand_hex_32__\n",
        encoding="utf-8",
    )

    changed = ensure_dev_env(tmp_path / ".env")
    values = _env_values(tmp_path / ".env")

    assert "SECRET_KEY" in changed
    assert not is_placeholder_secret(values["SECRET_KEY"])
    assert values["SECRET_KEY"] != "__CHANGE_ME_GENERATE_WITH_openssl_rand_hex_32__"


def test_compose_uses_docker_env_values_for_interpolation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "configs" / ".env.docker.core"
    env_file.parent.mkdir()
    env_file.write_text(
        "DB_PASSWORD=docker-password\n"
        "BASELITH_HTTP_PORT=9123\n"
        "COMPOSE_PROJECT_NAME=isolated-project\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DB_PASSWORD", "local-password")
    monkeypatch.setenv("BASELITH_HTTP_PORT", "8024")

    captured = {}

    def fake_run(command, env, check):
        captured["command"] = command
        captured["env"] = env
        captured["check"] = check

        class Result:
            returncode = 0

        return Result()

    with patch.object(add_docker.subprocess, "run", side_effect=fake_run):
        assert add_docker._compose(["ps"]) == 0

    assert captured["env"]["DB_PASSWORD"] == "docker-password"
    assert captured["env"]["BASELITH_HTTP_PORT"] == "8024"
    assert captured["env"]["BASELITH_DOCKER_ENV_FILE"] == "configs/.env.docker.core"
    assert captured["command"][:4] == [
        "docker",
        "compose",
        "--env-file",
        "configs/.env.docker.core",
    ]
    assert captured["check"] is False


def test_compose_uses_persisted_core_image_for_plugin_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "configs" / ".env.docker.core"
    env_file.parent.mkdir()
    env_file.write_text(
        "BASELITH_CORE_IMAGE=ghcr.io/baselithcore/baselithcore:docker-runtime-test\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("BASELITH_CORE_IMAGE", raising=False)

    captured = {}

    def fake_run(command, env, check):
        captured["command"] = command
        captured["env"] = env
        captured["check"] = check

        class Result:
            returncode = 0

        return Result()

    with patch.object(add_docker.subprocess, "run", side_effect=fake_run):
        assert add_docker._compose(["build", "api"]) == 0

    assert (
        captured["env"]["BASELITH_CORE_IMAGE"]
        == "ghcr.io/baselithcore/baselithcore:docker-runtime-test"
    )


def test_shell_core_image_overrides_persisted_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "configs" / ".env.docker.core"
    env_file.parent.mkdir()
    env_file.write_text(
        "BASELITH_CORE_IMAGE=ghcr.io/baselithcore/baselithcore:persisted\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "BASELITH_CORE_IMAGE", "ghcr.io/baselithcore/baselithcore:shell"
    )

    env = add_docker._compose_environment(env_file)

    assert env["BASELITH_CORE_IMAGE"] == "ghcr.io/baselithcore/baselithcore:shell"


def test_set_docker_core_image_persists_selected_runtime_image(tmp_path):
    env_file = tmp_path / "configs" / ".env.docker.core"

    assert set_docker_core_image("ghcr.io/baselithcore/baselithcore:test", env_file)
    assert "BASELITH_CORE_IMAGE=ghcr.io/baselithcore/baselithcore:test" in (
        env_file.read_text(encoding="utf-8")
    )
    assert not set_docker_core_image(
        "ghcr.io/baselithcore/baselithcore:test", env_file
    )


def _env_values(path: Path) -> dict[str, str]:
    return {
        key.strip(): value.strip()
        for key, value in (
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.strip().startswith("#")
        )
    }


def test_repeated_setup_preserves_credentials_and_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensure_dev_env()
    ensure_docker_core_env()
    local = (tmp_path / ".env").read_text()
    docker = (tmp_path / "configs" / ".env.docker.core").read_text()
    assert ensure_dev_env() == []
    assert ensure_docker_core_env() == []
    assert (tmp_path / ".env").read_text() == local
    assert (tmp_path / "configs" / ".env.docker.core").read_text() == docker


def test_new_checkouts_get_distinct_secrets_and_projects(tmp_path, monkeypatch):
    profiles = []
    for name in ("first", "second"):
        checkout = tmp_path / name
        checkout.mkdir()
        monkeypatch.chdir(checkout)
        ensure_dev_env()
        ensure_docker_core_env()
        profiles.append(_env_values(checkout / "configs" / ".env.docker.core"))
    for key in ("SECRET_KEY", "DB_PASSWORD", "COMPOSE_PROJECT_NAME"):
        assert profiles[0][key] != profiles[1][key]
