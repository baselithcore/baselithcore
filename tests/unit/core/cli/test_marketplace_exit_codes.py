"""Exit codes for `baselith plugin marketplace ...`.

Every one of these commands used to print its outcome and return ``None``.
``main()`` coerces a non-int result to 0, so a failed install, a plugin that
does not exist, a rejected publish and a failed login all reported SUCCESS to
the shell — and to any CI step running them. A provisioning script could not
tell whether it had provisioned anything.

These tests pin the code each outcome returns, so the shell can act on it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.cli.commands import plugin as plugin_pkg
from core.cli.commands.plugin import marketplace as m


def _result(status: str, **extra):
    """An installer result: `.status.value` plus whatever else is read."""
    return SimpleNamespace(
        status=SimpleNamespace(value=status),
        destination=extra.pop("destination", "/plugins/x"),
        error=extra.pop("error", "boom"),
        **extra,
    )


def _plugin(name: str = "acme"):
    return SimpleNamespace(
        id=name,
        name=name,
        status=SimpleNamespace(value="verified"),
        author="someone",
        description="d",
        git_url=None,
        tags=[],
        stars=0,
        downloads=0,
    )


@pytest.fixture
def registry():
    with patch.object(m, "PluginRegistry") as factory:
        yield factory.return_value


@pytest.fixture
def installer():
    with patch.object(m, "PluginInstaller") as factory:
        yield factory.return_value


class TestUnknownPluginFails:
    """A plugin the marketplace does not have is not a success."""

    def test_info(self, registry):
        registry.get_plugin = AsyncMock(return_value=None)

        assert m.info_plugin("absent") == 1

    def test_install(self, registry, installer):
        registry.get_plugin = AsyncMock(return_value=None)

        assert m.install_plugin_cmd("absent") == 1

    def test_update(self, registry, installer):
        registry.get_plugin = AsyncMock(return_value=None)

        assert m.update_plugin_cmd("absent") == 1


class TestInstall:
    def test_success(self, registry, installer):
        registry.get_plugin = AsyncMock(return_value=_plugin())
        installer.install = AsyncMock(return_value=_result("success"))

        assert m.install_plugin_cmd("acme") == 0

    def test_already_installed_is_a_success(self, registry, installer):
        """The requested end state is reached, so a re-run must not fail."""
        registry.get_plugin = AsyncMock(return_value=_plugin())
        installer.install = AsyncMock(return_value=_result("already_installed"))

        assert m.install_plugin_cmd("acme") == 0

    def test_failure(self, registry, installer):
        registry.get_plugin = AsyncMock(return_value=_plugin())
        installer.install = AsyncMock(return_value=_result("failed"))

        assert m.install_plugin_cmd("acme") == 1


class TestUninstall:
    def test_success(self, installer):
        installer.uninstall = AsyncMock(return_value=True)

        assert m.uninstall_plugin_cmd("acme") == 0

    def test_failure(self, installer):
        installer.uninstall = AsyncMock(return_value=False)

        assert m.uninstall_plugin_cmd("acme") == 1


class TestSearch:
    def test_results_found(self, registry):
        registry.search = AsyncMock(return_value=[_plugin()])

        assert m.search_plugins("x") == 0

    def test_no_results_is_still_a_success(self, registry):
        """The query ran; "nothing matched" is an answer, not a failure."""
        registry.search = AsyncMock(return_value=[])

        assert m.search_plugins("x") == 0

    def test_an_invalid_category_fails(self, registry):
        registry.search = AsyncMock(return_value=[])

        assert m.search_plugins("x", category="not-a-category") == 1

    def test_a_registry_error_fails(self, registry):
        registry.search = AsyncMock(side_effect=RuntimeError("offline"))

        assert m.search_plugins("x") == 1


class TestPublish:
    def test_success(self):
        with (
            patch.object(m, "CredentialsManager") as creds,
            patch.object(m, "PluginPublisher") as publisher,
        ):
            creds.return_value.load_api_key = AsyncMock(return_value="k")
            creds.return_value.load_token = AsyncMock(return_value=None)
            publisher.return_value.publish = AsyncMock(
                return_value={
                    "status": "success",
                    "data": {"name": "a", "version": "1"},
                }
            )

            assert m.publish_plugin_cmd(".") == 0

    def test_rejection_fails(self):
        """The one that mattered most: a rejected publish looked shipped."""
        with (
            patch.object(m, "CredentialsManager") as creds,
            patch.object(m, "PluginPublisher") as publisher,
        ):
            creds.return_value.load_api_key = AsyncMock(return_value="k")
            creds.return_value.load_token = AsyncMock(return_value=None)
            publisher.return_value.publish = AsyncMock(
                return_value={"status": "error", "message": "rejected", "issues": ["x"]}
            )

            assert m.publish_plugin_cmd(".") == 1

    def test_missing_credentials_fail(self, monkeypatch):
        monkeypatch.delenv("MARKETPLACE_API_KEY", raising=False)
        with patch.object(m, "CredentialsManager") as creds:
            creds.return_value.load_api_key = AsyncMock(return_value=None)
            creds.return_value.load_token = AsyncMock(return_value=None)

            assert m.publish_plugin_cmd(".") == 1


class TestAuth:
    def test_github_login_success(self):
        with patch.object(m, "AuthService") as auth:
            auth.return_value.login_with_github = AsyncMock(
                return_value={"status": "success", "user": {"login": "someone"}}
            )

            assert m.login_cmd(github_token="gh") == 0

    def test_github_login_rejection_fails(self):
        with patch.object(m, "AuthService") as auth:
            auth.return_value.login_with_github = AsyncMock(
                return_value={"status": "error", "message": "bad token"}
            )

            assert m.login_cmd(github_token="gh") == 1

    def test_logout_always_succeeds(self):
        with patch.object(m, "CredentialsManager") as creds:
            creds.return_value.delete_credentials = AsyncMock()

            assert m.logout_cmd() == 0

    def test_identity_when_authenticated(self):
        with (
            patch.object(m, "AuthService") as auth,
            patch.object(m, "CredentialsManager") as creds,
        ):
            creds.return_value.load_token = AsyncMock(return_value="jwt")
            auth.return_value.get_current_identity = AsyncMock(
                return_value={"status": "success", "user": {"email": "a@b.c"}}
            )

            assert m.identity_cmd() == 0

    def test_identity_when_not_authenticated_fails(self):
        """A script asking "am I logged in?" needs that in the exit status."""
        with (
            patch.object(m, "AuthService"),
            patch.object(m, "CredentialsManager") as creds,
        ):
            creds.return_value.load_token = AsyncMock(return_value=None)
            creds.return_value.load_api_key = AsyncMock(return_value=None)

            assert m.identity_cmd() == 1

    def test_identity_with_an_unverifiable_token_fails(self):
        with (
            patch.object(m, "AuthService") as auth,
            patch.object(m, "CredentialsManager") as creds,
        ):
            creds.return_value.load_token = AsyncMock(return_value="stale")
            auth.return_value.get_current_identity = AsyncMock(
                return_value={"status": "error", "message": "expired"}
            )

            assert m.identity_cmd() == 1


class TestTheDispatcherPassesItOn:
    """`dispatch_plugin` must not swallow the code it now receives."""

    def test_a_failing_command_reaches_the_shell(self):
        from core.cli import handlers_plugin

        args = MagicMock()
        args.plugin_command = "marketplace"
        args.marketplace_command = "info"
        args.plugin_id = "absent"

        # dispatch_plugin resolves the command through the PACKAGE
        # (`from core.cli.commands import plugin`), so patching the module
        # that defines it leaves the package's own binding untouched.
        with patch.object(plugin_pkg, "info_plugin", return_value=1):
            assert handlers_plugin.dispatch_plugin(args) == 1

    def test_a_succeeding_command_reaches_the_shell(self):
        from core.cli import handlers_plugin

        args = MagicMock()
        args.plugin_command = "marketplace"
        args.marketplace_command = "info"
        args.plugin_id = "present"

        with patch.object(plugin_pkg, "info_plugin", return_value=0):
            assert handlers_plugin.dispatch_plugin(args) == 0
