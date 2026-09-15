from __future__ import annotations

from unittest.mock import Mock, call

from core.cli.commands import up


def test_up_prepares_pulls_starts_and_checks_health(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    compose = Mock(return_value=0)
    health = Mock(return_value=True)
    monkeypatch.setattr(up, "_docker_available", Mock(return_value=True))
    monkeypatch.setattr(up, "_compose", compose)
    monkeypatch.setattr(up, "_wait_for_health", health)

    image = "ghcr.io/baselithcore/baselithcore:docker-runtime-test"
    assert up.run_up(image=image, timeout=12) == 0

    assert (tmp_path / "docker-compose.core.yml").is_file()
    assert (tmp_path / "Dockerfile").is_file()
    assert (tmp_path / "configs" / ".env.docker.core").is_file()
    env = compose.call_args_list[0].args[1]
    assert env["BASELITH_CORE_IMAGE"] == image
    assert compose.call_args_list == [
        call(["pull", "postgres", "redis", "qdrant"], env),
        call(["build", "--pull", "api"], env),
        call(["up", "-d"], env),
    ]
    health.assert_called_once_with("8000", 12)


def test_up_refuses_unrelated_docker_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    monkeypatch.setattr(up, "_docker_available", Mock(return_value=True))

    assert up.run_up() == 1
    assert (tmp_path / "Dockerfile").read_text(encoding="utf-8") == "FROM alpine\n"


def test_up_reports_failed_service_pull(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(up, "_docker_available", Mock(return_value=True))
    monkeypatch.setattr(up, "_compose", Mock(return_value=1))

    assert up.run_up() == 1


def test_up_preserves_existing_runtime_configuration(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("ARG BASELITH_CORE_IMAGE\n# customized\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.core.yml"
    compose.write_text("# BASELITH_CORE_IMAGE\nservices: {}\n", encoding="utf-8")

    created = up._ensure_runtime_project(tmp_path)

    assert dockerfile.read_text(encoding="utf-8").endswith("# customized\n")
    assert compose.read_text(encoding="utf-8").endswith("services: {}\n")
    assert tmp_path / "configs" / "plugins.yaml" in created
