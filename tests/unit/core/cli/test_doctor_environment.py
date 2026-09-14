"""`baselith doctor` must not fail a correctly configured container.

The environment check used to look only for a `.env` file on disk. Kubernetes
injects the ConfigMap and Secret through `envFrom` and there is deliberately no
file, so the check reported FAIL on the recommended production setup — and its
remediation told the operator to copy a template into a read-only rootfs.
A diagnostic that fails the right answer teaches people to ignore diagnostics.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.cli.commands.doctor import _ENV_CONFIG_MARKERS, check_env_file


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run from an empty cwd with none of the markers set.

    `core.config` loads the repository .env into os.environ at import, so
    without this the developer's own values decide the result.
    """
    monkeypatch.chdir(tmp_path)
    for name in _ENV_CONFIG_MARKERS:
        monkeypatch.delenv(name, raising=False)


def test_passes_when_configuration_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "s3cr3t")
    monkeypatch.setenv("DB_HOST", "postgres")
    result = check_env_file()
    assert result.passed, result.message
    assert "environment" in result.message.lower()


def test_passes_when_a_dotenv_file_exists(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_ENV=development\n", encoding="utf-8")
    result = check_env_file()
    assert result.passed, result.message
    assert ".env" in result.message


def test_fails_when_nothing_is_configured() -> None:
    """The real failure — no file and no environment — must still fail."""
    assert not check_env_file().passed


@pytest.mark.parametrize("marker", _ENV_CONFIG_MARKERS)
def test_every_marker_is_absent_from_a_bare_process(marker: str) -> None:
    """A marker the image sets by default would make the check always pass.

    The Dockerfile's own ENV block covers HOST/PORT/WEB_CONCURRENCY and the
    Ollama defaults; none of those belong here, or the check stops meaning
    "somebody configured this".
    """
    assert marker not in {"HOST", "PORT", "WEB_CONCURRENCY", "OLLAMA_API_BASE"}
    assert os.environ.get(marker) is None
