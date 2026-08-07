"""The Art. 7 consent log must be reachable from a data-subject request.

Consent records are personal data. If the consent service is not attached to
the DSR registry, an access or erasure request completes successfully while
leaving that store untouched — a silent gap that looks like compliance.
"""

from __future__ import annotations

import pytest

from core.api.startup_checks import register_consent_provider


def reset_privacy_config() -> None:
    """Drop the cached privacy config so the env is re-read (no public reset)."""
    import core.config.privacy as privacy_config

    privacy_config._privacy_config = None


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    """Isolate the privacy singletons and the config between tests."""
    import core.privacy.consent as consent
    import core.privacy.service as service

    # The provider registry is a process-global shared by every consumer, so it
    # has to be cleared too or providers leak between tests.
    def _clear() -> None:
        consent.reset_consent_service()
        service._service = None
        service._registry.unregister("consent")
        reset_privacy_config()

    _clear()
    yield
    _clear()


def _provider_names() -> list[str]:
    from core.privacy import get_data_subject_service

    return [p.name for p in get_data_subject_service().registry.all()]


def test_registration_is_a_no_op_while_privacy_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in with the rest of the subsystem — nothing is attached by default."""
    monkeypatch.delenv("PRIVACY_ENABLED", raising=False)
    reset_privacy_config()

    register_consent_provider()

    assert "consent" not in _provider_names()


def test_consent_is_registered_when_privacy_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRIVACY_ENABLED", "true")
    reset_privacy_config()

    register_consent_provider()

    assert "consent" in _provider_names()


def test_registration_is_idempotent_and_refreshes_the_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second pass must not duplicate — and must not keep a stale service.

    The registry is keyed by name, so re-registering replaces. That matters
    after a reconfiguration: keeping the old instance would point the DSR at a
    consent store nobody writes to any more.
    """
    import core.privacy.consent as consent
    from core.privacy import get_consent_service, get_data_subject_service

    monkeypatch.setenv("PRIVACY_ENABLED", "true")
    reset_privacy_config()

    register_consent_provider()
    consent.reset_consent_service()  # simulate a reconfiguration
    register_consent_provider()

    assert _provider_names().count("consent") == 1
    assert get_data_subject_service().registry.get("consent") is get_consent_service()


async def test_a_subject_export_then_reaches_the_consent_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the wiring: Art. 15 and Art. 17 actually cover consent."""
    monkeypatch.setenv("PRIVACY_ENABLED", "true")
    reset_privacy_config()

    from core.privacy import get_consent_service, get_data_subject_service

    await get_consent_service().grant("u-1", "analytics", notice_version="v3")
    register_consent_provider()

    bundle = await get_data_subject_service().export_subject("u-1")
    assert "consent" in bundle.data
    assert bundle.data["consent"][0]["purpose"] == "analytics"

    report = await get_data_subject_service().erase_subject("u-1")
    assert report.erased["consent"] == 1
    assert await get_consent_service().history("u-1") == []
