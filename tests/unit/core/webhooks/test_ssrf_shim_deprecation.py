"""The ``baselith.core.webhooks.ssrf`` shim: deprecated since 1.0, removed in 2.0.

The shim must keep behaving exactly like the shared guard it delegates to
(callers written against it keep their security), announce its removal on
every call, and no longer be what the framework itself uses.
"""

from __future__ import annotations

import ast
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import baselith.core.webhooks as webhooks_pkg
from baselith.core.security.ssrf import SsrfError
from baselith.core.stability import STABILITY_ATTRIBUTE, Stability
from baselith.core.webhooks import ssrf as shim

REPO = Path(__file__).resolve().parents[4]


def _fake_getaddrinfo(ips: list[str]):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]

    return fake


class TestDeprecationAnnouncement:
    def test_validate_webhook_url_warns_with_version_and_replacement(self):
        with pytest.warns(DeprecationWarning) as caught:
            shim.validate_webhook_url("http://127.0.0.1/h", allow_internal=True)
        message = str(caught[0].message)
        assert "since 1.0" in message
        assert "removed in 2.0" in message
        assert "baselith.core.security.ssrf.assert_url_safe" in message

    def test_resolve_pinned_target_warns_with_version_and_replacement(self):
        with pytest.warns(DeprecationWarning) as caught:
            shim.resolve_pinned_target("http://127.0.0.1/h", allow_internal=True)
        message = str(caught[0].message)
        assert "removed in 2.0" in message
        assert "baselith.core.security.ssrf.resolve_pinned_target" in message

    @pytest.mark.parametrize("name", ["validate_webhook_url", "resolve_pinned_target"])
    def test_functions_are_marked_deprecated(self, name):
        assert getattr(getattr(shim, name), STABILITY_ATTRIBUTE) is Stability.DEPRECATED

    def test_the_error_alias_is_the_shared_class(self):
        # An alias, not a subclass: ``except WebhookSSRFError`` must keep
        # catching what the new API raises.
        assert shim.WebhookSSRFError is SsrfError

    def test_package_reexports_are_the_deprecated_shim_objects(self):
        assert webhooks_pkg.validate_webhook_url is shim.validate_webhook_url
        assert webhooks_pkg.resolve_pinned_target is shim.resolve_pinned_target
        assert webhooks_pkg.WebhookSSRFError is SsrfError
        for name in (
            "WebhookSSRFError",
            "resolve_pinned_target",
            "validate_webhook_url",
        ):
            assert name in webhooks_pkg.__all__

    def test_importing_the_shim_does_not_warn(self):
        # Only a call warns: the package re-exports it, so an import-time
        # warning would fire for every user of baselith.core.webhooks. Fresh
        # interpreter, so the import really runs (a reload here would replace
        # the function objects other tests compare by identity).
        result = subprocess.run(
            [
                sys.executable,
                "-W",
                "error::DeprecationWarning:baselith.core.webhooks.ssrf",
                "-c",
                "import baselith.core.webhooks.ssrf",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr


class TestShimStillEnforces:
    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    @pytest.mark.parametrize(
        "url", ["http://127.0.0.1/h", "http://169.254.169.254/x", "ftp://example.com/"]
    )
    def test_validate_rejects_unsafe_targets(self, url):
        with pytest.raises(shim.WebhookSSRFError):
            shim.validate_webhook_url(url)

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_validate_rejects_a_name_resolving_internal(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["10.0.0.5"]))
        with pytest.raises(SsrfError):
            shim.validate_webhook_url("https://rebind.example.com/")

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_resolve_pins_to_the_validated_address(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
        assert shim.resolve_pinned_target("https://example.com:8443/p") == (
            "https://93.184.216.34:8443/p",
            "example.com",
        )

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_allow_internal_still_checks_the_scheme(self):
        with pytest.raises(SsrfError):
            shim.validate_webhook_url("file:///etc/passwd", allow_internal=True)


def test_no_framework_module_imports_the_shim():
    """Every internal caller uses baselith.core.security.ssrf directly."""
    offenders = []
    for path in (REPO / "baselith").rglob("*.py"):
        if path == REPO / "baselith/core/webhooks/ssrf.py":
            continue
        if path == REPO / "baselith/core/webhooks/__init__.py":
            continue  # the deprecated, documented re-export
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "baselith.core.webhooks.ssrf"
            ):
                offenders.append(str(path.relative_to(REPO)))
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "baselith.core.webhooks"
            ):
                names = {alias.name for alias in node.names}
                if names & {
                    "ssrf",
                    "WebhookSSRFError",
                    "validate_webhook_url",
                    "resolve_pinned_target",
                }:
                    offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
