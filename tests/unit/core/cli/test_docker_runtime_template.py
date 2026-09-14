from __future__ import annotations

import yaml

from core import __version__
from core.cli.commands.init import run_init


def test_docker_runtime_template_is_standalone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert run_init("customer-core", "docker-runtime") == 0

    project = tmp_path / "customer-core"
    compose = yaml.safe_load((project / "docker-compose.core.yml").read_text())
    compose_text = (project / "docker-compose.core.yml").read_text()
    dockerfile = (project / "Dockerfile").read_text()
    env = (project / "configs" / ".env.docker.core").read_text()

    assert not (project / "core").exists()
    assert compose["services"]["api"]["build"]["context"] == "."
    assert "${{" not in compose_text
    assert f"ghcr.io/baselithcore/baselithcore:{__version__}" in dockerfile
    assert "COPY configs/plugin-requirements.txt" in dockerfile
    assert (project / "plugins" / "__init__.py").is_file()
    assert (project / "configs" / "plugins.yaml").read_text() == "{}\n"
    assert "DB_PASSWORD=" in env
    assert "SECRET_KEY=" in env
    assert "COMPOSE_PROJECT_NAME=customer-core-" in env


def test_docker_runtime_template_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing").mkdir()

    assert run_init("existing", "docker-runtime") == 1
