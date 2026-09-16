from core.cli.commands import up


def test_up_prepares_runtime_and_persists_selected_image(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(up, "DOCKER_BIN", "docker")
    monkeypatch.setattr(up, "_docker_available", lambda: True)
    monkeypatch.setattr(up, "_wait_for_health", lambda port, timeout: True)

    calls = []

    def fake_compose(args, env):
        calls.append((args, env.copy()))
        return 0

    monkeypatch.setattr(up, "_compose", fake_compose)

    image = "ghcr.io/baselithcore/baselithcore:docker-runtime-test"

    assert up.run_up(image=image, timeout=1) == 0

    assert (tmp_path / "Dockerfile").is_file()
    assert (tmp_path / "docker-compose.core.yml").is_file()
    env_file = tmp_path / "configs" / ".env.docker.core"
    assert env_file.is_file()
    assert f"BASELITH_CORE_IMAGE={image}" in env_file.read_text(encoding="utf-8")
    assert [call[0] for call in calls] == [
        ["pull", "postgres", "redis", "qdrant"],
        ["build", "--pull", "api"],
        ["up", "-d"],
    ]
    assert calls[1][1]["BASELITH_CORE_IMAGE"] == image


def test_up_refuses_to_replace_unrelated_dockerfile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(up, "DOCKER_BIN", "docker")
    monkeypatch.setattr(up, "_docker_available", lambda: True)
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")

    assert up.run_up(image="example/core:test", timeout=1) == 1


def test_runtime_template_uses_configurable_core_image():
    from core.cli.templates.docker_runtime import DOCKER_RUNTIME_FILES

    assert "BASELITH_CORE_IMAGE" in DOCKER_RUNTIME_FILES["Dockerfile"]
    assert "BASELITH_CORE_IMAGE" in DOCKER_RUNTIME_FILES["docker-compose.core.yml"]
    assert "baselith up" in DOCKER_RUNTIME_FILES["README.md"]
