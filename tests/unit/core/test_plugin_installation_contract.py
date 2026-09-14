"""Installation regressions without Docker, network or host plugin packages."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from core.cli.commands.plugin import add, add_docker
from core.cli.commands.plugin.install_validation import validate_install_manifest


def plugin(tmp_path, name="sample", **extra):
    path = tmp_path / "plugins" / name
    path.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": "1.0.0", "description": "Test", **extra}
    (path / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    (path / "plugin.py").write_text("class Sample(Plugin): pass\n")
    return path, manifest


def test_docker_add_does_not_check_host_packages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plugin(tmp_path)
    validate = Mock(side_effect=AssertionError("Host validation must not run"))
    monkeypatch.setattr(add, "validate_local_plugin", validate)
    monkeypatch.setattr(add, "deps_check", validate)
    monkeypatch.setattr(add, "enable_local_plugin", Mock(return_value=0))
    install = Mock(return_value=0)
    monkeypatch.setattr(add_docker, "install_plugin_into_docker", install)
    assert add.add_plugin("plugin-sample", docker=True) == 0
    install.assert_called_once()


def test_existing_dependency_prepares_transitive_dependencies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plugin(tmp_path, "existing", plugin_dependencies={"missing": ">=1.0.0"})
    _, manifest = plugin(tmp_path, plugin_dependencies={"existing": ">=1.0.0"})
    cloned = []

    def clone(source, path, ref):
        cloned.append(path.name)
        plugin(tmp_path, path.name)
        return True

    monkeypatch.setattr(add, "_clone", clone)
    enable = Mock(return_value=0)
    monkeypatch.setattr(add, "enable_local_plugin", enable)
    monkeypatch.setattr(
        add, "deps_check", Mock(side_effect=AssertionError("Host check"))
    )
    assert add._install_plugin_dependencies(manifest, docker=True)
    assert cloned == ["missing"]
    assert [call.args[0] for call in enable.call_args_list] == ["missing", "existing"]


@pytest.mark.parametrize(
    "bounds",
    [
        {"min_core_version": "999.0.0"},
        {"max_core_version": "0.0.1"},
        {"min_core_version": "not-a-version"},
        {"min_core_version": 123},
    ],
)
def test_invalid_core_bounds_fail_before_enable(tmp_path, monkeypatch, bounds):
    monkeypatch.chdir(tmp_path)
    plugin(tmp_path, **bounds)
    enable = Mock()
    monkeypatch.setattr(add, "enable_local_plugin", enable)
    assert add.add_plugin("plugin-sample", docker=True) == 1
    enable.assert_not_called()


@pytest.mark.parametrize(
    "extra",
    [
        {"python_dependencies": "requests"},
        {"python_dependencies": ["requests\n--index-url https://example.test"]},
        {"plugin_dependencies": {"../outside": ">=1.0.0"}},
        {"plugin_dependencies": {"auth": "bad"}},
        {"frontend": {"package_manager": "unknown"}},
        {"frontend": {"path": ["ui"]}},
        {"health_endpoint": "http://example.test/"},
    ],
)
def test_invalid_build_inputs_rejected(extra):
    assert validate_install_manifest({"version": "1.0.0", **extra})


def test_frontend_false_disables_legacy_detection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, manifest = plugin(tmp_path, frontend=False)
    (path / "ui").mkdir()
    (path / "ui" / "package.json").write_text("{}")
    assert add_docker._frontend_config("sample", manifest) is None


def test_empty_frontend_output_is_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, manifest = plugin(tmp_path, frontend={"path": "ui", "output_dir": "dist"})
    (path / "ui" / "dist").mkdir(parents=True)
    monkeypatch.setattr(
        add_docker.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0))
    )
    assert add_docker._build_frontend("sample", manifest) == 1
    (path / "ui" / "dist" / "index.html").write_text("test")
    assert add_docker._build_frontend("sample", manifest) == 0


def test_requirements_prefer_yaml_and_preserve_unchanged_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, _ = plugin(tmp_path, python_dependencies=["httpx>=0.27", "httpx>=0.27"])
    (path / "manifest.json").write_text(json.dumps({"python_dependencies": ["wrong"]}))
    plugin(tmp_path, "disabled", python_dependencies=["unwanted"])
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "plugins.yaml").write_text("sample:\n  enabled: true\n")
    add_docker._write_plugin_requirements()
    target = add_docker.PLUGIN_REQUIREMENTS
    assert target.read_text().splitlines()[1:] == ["httpx>=0.27"]
    modified = target.stat().st_mtime_ns
    add_docker._write_plugin_requirements()
    assert target.stat().st_mtime_ns == modified


def test_invalid_manifest_preserves_previous_requirements(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, _ = plugin(tmp_path)
    (path / "manifest.yaml").write_text("name: [broken")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "plugins.yaml").write_text("sample: true\n")
    add_docker.PLUGIN_REQUIREMENTS.write_text("previous\n")
    with pytest.raises(ValueError):
        add_docker._write_plugin_requirements()
    assert add_docker.PLUGIN_REQUIREMENTS.read_text() == "previous\n"


@pytest.mark.parametrize("shell_port, expected", [(None, "8123"), ("9123", "9123")])
def test_health_probe_uses_compose_port(tmp_path, monkeypatch, shell_port, expected):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    add_docker.DOCKER_ENV_FILE.write_text('BASELITH_HTTP_PORT="8123" # comment\n')
    monkeypatch.delenv("BASELITH_HTTP_PORT", raising=False)
    if shell_port:
        monkeypatch.setenv("BASELITH_HTTP_PORT", shell_port)
    response = SimpleNamespace(status_code=200)
    opener = Mock(return_value=response)
    monkeypatch.setattr(add_docker.httpx, "get", opener)
    assert add_docker._wait_for_http("/health", 200)
    assert opener.call_args.args[0] == f"http://localhost:{expected}/health"
    assert opener.call_args.kwargs["follow_redirects"] is False
    assert opener.call_args.kwargs["trust_env"] is False


def test_unexpected_http_status_backs_off(monkeypatch):
    response = SimpleNamespace(status_code=202)
    monkeypatch.setattr(add_docker.httpx, "get", Mock(return_value=response))
    monkeypatch.setattr(add_docker.time, "monotonic", Mock(side_effect=[0, 0, 3]))
    sleep = Mock()
    monkeypatch.setattr(add_docker.time, "sleep", sleep)
    assert not add_docker._wait_for_http("/health", 200, timeout=2)
    sleep.assert_called_once_with(2)


def test_dependency_frontends_build_before_parent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plugin(tmp_path, "base", frontend={"path": "ui"})
    plugin(
        tmp_path,
        "left",
        plugin_dependencies={"base": ">=1.0.0"},
        frontend={"path": "ui"},
    )
    _, manifest = plugin(
        tmp_path, plugin_dependencies={"left": ">=1.0.0", "base": ">=1.0.0"}
    )
    build = Mock(return_value=0)
    monkeypatch.setattr(add_docker, "_build_frontend", build)
    assert add_docker._build_frontends("sample", manifest) == 0
    assert [call.args[0] for call in build.call_args_list] == ["base", "left", "sample"]


def test_legacy_dependency_does_not_infer_output_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, _ = plugin(tmp_path, "base")
    (path / "ui").mkdir()
    (path / "ui" / "package.json").write_text('{"scripts": {"build": "vite build"}}')
    _, manifest = plugin(tmp_path, plugin_dependencies={"base": ">=1.0.0"})
    build = Mock(return_value=0)
    monkeypatch.setattr(add_docker, "_build_frontend", build)
    assert add_docker._build_frontends("sample", manifest) == 0
    assert [call.args[0] for call in build.call_args_list] == ["sample"]


def test_force_preserves_directory_when_git_status_fails(tmp_path, monkeypatch):
    path, _ = plugin(tmp_path)
    monkeypatch.setattr(add.shutil, "which", Mock(return_value="/usr/bin/git"))
    monkeypatch.setattr(
        add.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=128, stdout="")),
    )
    assert not add._remove_existing(path)
    assert (path / "plugin.py").exists()


@pytest.mark.parametrize("deps", [{"sample": ">=1.0.0"}, {"base": ">=2.0.0"}])
def test_cycle_or_dependency_version_mismatch_stops_build(tmp_path, monkeypatch, deps):
    monkeypatch.chdir(tmp_path)
    plugin(tmp_path, "base")
    _, manifest = plugin(tmp_path, plugin_dependencies=deps)
    build = Mock(return_value=0)
    monkeypatch.setattr(add_docker, "_build_frontend", build)
    assert add_docker._build_frontends("sample", manifest) == 1
    build.assert_not_called()


@pytest.mark.parametrize("failure", ["frontend", "image", "up", "health", "plugin"])
def test_installation_failure_never_reports_ready(monkeypatch, failure):
    monkeypatch.setattr(add_docker, "_write_plugin_requirements", Mock())
    monkeypatch.setattr(
        add_docker, "_build_frontends", Mock(return_value=int(failure == "frontend"))
    )
    monkeypatch.setattr(
        add_docker,
        "_compose",
        Mock(
            side_effect=lambda args: int(
                args[0] == {"image": "build", "up": "up"}.get(failure)
            )
        ),
    )
    monkeypatch.setattr(
        add_docker, "_wait_for_http", Mock(return_value=failure != "health")
    )
    monkeypatch.setattr(
        add_docker, "_probe_plugin", Mock(return_value=failure != "plugin")
    )
    success = Mock()
    monkeypatch.setattr(add_docker, "print_success", success)
    assert add_docker.install_plugin_into_docker("sample", {}) == 1
    assert "Plugin ready" not in [call.args[0] for call in success.call_args_list]
