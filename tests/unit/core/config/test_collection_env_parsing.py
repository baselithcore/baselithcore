"""Collection-typed settings must survive the values the docs tell people to use.

pydantic-settings JSON-decodes any ``list``/``set``/``dict``/``tuple`` field in
``EnvSettingsSource`` *before* any validator runs, so a comma-separated or blank
environment value raises ``SettingsError`` out of the **entire settings class** —
not just that field. Several of these keys are documented in ``.env.example``
with exactly the values that fail, so an operator following the template breaks
a whole subsystem (the task queue, document processing, the guardrails).

Every test here drives the **environment**, not a Python value: passing a real
list to the constructor bypasses ``EnvSettingsSource`` entirely and so cannot
reproduce the bug.
"""

from __future__ import annotations

import pytest

from core.config._collections import csv_list

pytestmark = [pytest.mark.unit]


def _env(monkeypatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)


class TestCsvList:
    def test_parses_a_comma_separated_string(self):
        assert csv_list("core.,plugins.") == ["core.", "plugins."]

    def test_trims_and_drops_empties(self):
        assert csv_list(" a , , b ") == ["a", "b"]

    def test_blank_is_the_empty_list(self):
        assert csv_list("") == []
        assert csv_list("   ") == []
        assert csv_list(None) == []

    def test_json_arrays_still_parse(self):
        """NoDecode stops pydantic decoding them, so we must — existing
        deployments have JSON in their environment."""
        assert csv_list('["a", "b"]') == ["a", "b"]

    def test_malformed_json_degrades_to_csv(self):
        assert csv_list("[a,b") == ["[a", "b"]

    def test_non_strings_pass_through(self):
        value = ["already", "a", "list"]
        assert csv_list(value) is value


class TestTaskQueueConfig:
    def test_replay_allowlist_accepts_the_documented_csv(self, monkeypatch):
        """The exact value `.env.example` documents."""
        from core.config.task_queue import TaskQueueConfig

        _env(monkeypatch, "TASK_QUEUE_DLQ_REPLAY_ALLOWED_MODULES", "core.,plugins.")
        assert TaskQueueConfig().dlq_replay_allowed_modules == ["core.", "plugins."]

    def test_replay_allowlist_accepts_blank(self, monkeypatch):
        from core.config.task_queue import TaskQueueConfig

        _env(monkeypatch, "TASK_QUEUE_DLQ_REPLAY_ALLOWED_MODULES", "")
        assert TaskQueueConfig().dlq_replay_allowed_modules == []

    def test_queues_accepts_csv(self, monkeypatch):
        from core.config.task_queue import TaskQueueConfig

        _env(monkeypatch, "TASK_QUEUE_QUEUES", "default,documents")
        assert TaskQueueConfig().queues == ["default", "documents"]

    def test_queues_accepts_blank(self, monkeypatch):
        from core.config.task_queue import TaskQueueConfig

        _env(monkeypatch, "TASK_QUEUE_QUEUES", "")
        assert TaskQueueConfig().queues == []

    def test_queues_still_accepts_json(self, monkeypatch):
        from core.config.task_queue import TaskQueueConfig

        _env(monkeypatch, "TASK_QUEUE_QUEUES", '["a", "b"]')
        assert TaskQueueConfig().queues == ["a", "b"]


class TestObservabilityConfig:
    def test_tenant_allowlist_accepts_csv(self, monkeypatch):
        from core.config.observability import ObservabilityConfig

        _env(monkeypatch, "METRICS_TENANT_LABEL_ALLOWLIST", "acme,globex")
        assert ObservabilityConfig().metrics_tenant_label_allowlist == {
            "acme",
            "globex",
        }

    def test_tenant_allowlist_accepts_blank(self, monkeypatch):
        from core.config.observability import ObservabilityConfig

        _env(monkeypatch, "METRICS_TENANT_LABEL_ALLOWLIST", "")
        assert ObservabilityConfig().metrics_tenant_label_allowlist == set()

    def test_a_configured_allowlist_actually_reaches_the_label(self, monkeypatch):
        """The user-visible consequence: a SettingsError here was swallowed by
        ``resolve_tenant_label``, collapsing every tenant to 'other' — the exact
        opposite of what the operator configured."""
        from core.config.observability import reset_observability_config
        from core.observability.metric_context import resolve_tenant_label

        _env(monkeypatch, "METRICS_TENANT_LABEL_ALLOWLIST", "acme,globex")
        reset_observability_config()
        try:
            assert resolve_tenant_label("acme") == "acme"
            assert resolve_tenant_label("someone-else") == "other"
        finally:
            reset_observability_config()


class TestProcessingConfig:
    def test_documents_extensions_accepts_the_documented_csv(self, monkeypatch):
        """`.env.example` ships ``DOCUMENTS_EXTENSIONS=pdf,docx,txt,md``.

        Parsed *and* dot-normalised — see
        ``test_dotless_extensions_are_normalised`` for why the dot matters.
        """
        from core.config.processing import ProcessingConfig

        _env(monkeypatch, "DOCUMENTS_EXTENSIONS", "pdf,docx,txt,md")
        assert ProcessingConfig().documents_extensions == (
            ".pdf",
            ".docx",
            ".txt",
            ".md",
        )

    def test_dotless_extensions_are_normalised(self, monkeypatch):
        """`.env.example:342` ships them dotless, but the filesystem source
        matches ``Path.suffix``, which always has a dot — so a dotless entry
        matched nothing and indexing silently found zero documents."""
        from core.config.processing import ProcessingConfig

        _env(monkeypatch, "DOCUMENTS_EXTENSIONS", "pdf,docx,txt,md")
        assert ProcessingConfig().documents_extensions == (
            ".pdf",
            ".docx",
            ".txt",
            ".md",
        )

    def test_dotted_extensions_are_left_alone(self, monkeypatch):
        from core.config.processing import ProcessingConfig

        _env(monkeypatch, "DOCUMENTS_EXTENSIONS", ".pdf,.DOCX")
        assert ProcessingConfig().documents_extensions == (".pdf", ".docx")

    def test_the_default_extensions_are_still_dotted(self):
        from core.config.processing import ProcessingConfig

        assert all(e.startswith(".") for e in ProcessingConfig().documents_extensions)

    def test_web_documents_urls_accepts_blank(self, monkeypatch):
        """`.env.example` ships this key blank — the shipped regression."""
        from core.config.processing import ProcessingConfig

        _env(monkeypatch, "WEB_DOCUMENTS_URLS", "")
        assert ProcessingConfig().web_documents_urls == []

    def test_web_documents_urls_accepts_csv(self, monkeypatch):
        from core.config.processing import ProcessingConfig

        _env(
            monkeypatch,
            "WEB_DOCUMENTS_URLS",
            "https://a.example/docs,https://b.example/docs",
        )
        assert ProcessingConfig().web_documents_urls == [
            "https://a.example/docs",
            "https://b.example/docs",
        ]

    def test_web_documents_allowlist_accepts_csv_and_blank(self, monkeypatch):
        from core.config.processing import ProcessingConfig

        _env(monkeypatch, "WEB_DOCUMENTS_ALLOWLIST", "a.example,b.example")
        assert ProcessingConfig().web_documents_allowlist == ["a.example", "b.example"]
        _env(monkeypatch, "WEB_DOCUMENTS_ALLOWLIST", "")
        assert ProcessingConfig().web_documents_allowlist == []

    def test_blank_mineru_model_source_means_auto(self, monkeypatch):
        """`.env.example` ships ``MINERU_MODEL_SOURCE=`` commented "empty = auto"."""
        from core.config.processing import ProcessingConfig

        _env(monkeypatch, "MINERU_MODEL_SOURCE", "")
        assert ProcessingConfig().mineru_model_source is None

    def test_a_real_mineru_model_source_still_validates(self, monkeypatch):
        from core.config.processing import ProcessingConfig

        _env(monkeypatch, "MINERU_MODEL_SOURCE", "huggingface")
        assert ProcessingConfig().mineru_model_source == "huggingface"


class TestAppConfig:
    def test_guardrail_keywords_accept_csv(self, monkeypatch):
        from core.config.app import AppConfig

        _env(monkeypatch, "CHAT_GUARDRAILS_BLOCK_KEYWORDS", "alpha,beta")
        assert AppConfig().chat_guardrails_block_keywords == ["alpha", "beta"]

    def test_guardrail_keywords_accept_blank(self, monkeypatch):
        from core.config.app import AppConfig

        _env(monkeypatch, "CHAT_GUARDRAILS_BLOCK_KEYWORDS", "")
        assert AppConfig().chat_guardrails_block_keywords == []

    def test_out_of_scope_patterns_accept_csv_and_blank(self, monkeypatch):
        from core.config.app import AppConfig

        _env(monkeypatch, "CHAT_GUARDRAILS_OUT_OF_SCOPE_PATTERNS", "^foo,^bar")
        assert AppConfig().chat_guardrails_out_of_scope_patterns == ["^foo", "^bar"]
        _env(monkeypatch, "CHAT_GUARDRAILS_OUT_OF_SCOPE_PATTERNS", "")
        assert AppConfig().chat_guardrails_out_of_scope_patterns == []


class TestSecurityConfig:
    """The worst instance of the class: `.env.example` shipped values that
    raised ``SettingsError`` out of ``SecurityConfig`` — so copying the
    template verbatim left the deployment with no API at all."""

    @pytest.fixture(autouse=True)
    def _minimum_valid_security_env(self, monkeypatch):
        """SecurityConfig has cross-field checks unrelated to this bug."""
        monkeypatch.setenv("SECRET_KEY", "s" * 48)
        monkeypatch.setenv("AUTH_REQUIRED", "false")

    def test_allow_origins_accepts_the_shipped_template_value(self, monkeypatch):
        """`.env.example:443` — ``ALLOW_ORIGINS=https://app.example.com``."""
        from core.config.security import SecurityConfig

        _env(monkeypatch, "ALLOW_ORIGINS", "https://app.example.com")
        assert SecurityConfig().allow_origins == ["https://app.example.com"]

    def test_allow_origins_accepts_multiple_csv_origins(self, monkeypatch):
        from core.config.security import SecurityConfig

        _env(monkeypatch, "ALLOW_ORIGINS", "https://a.example,https://b.example")
        assert SecurityConfig().allow_origins == [
            "https://a.example",
            "https://b.example",
        ]

    def test_allow_origins_accepts_blank(self, monkeypatch):
        from core.config.security import SecurityConfig

        _env(monkeypatch, "ALLOW_ORIGINS", "")
        assert SecurityConfig().allow_origins == []

    def test_trusted_hosts_still_accepts_the_shipped_json(self, monkeypatch):
        """`.env.example:448` ships a JSON array — NoDecode must not break it."""
        from core.config.security import SecurityConfig

        _env(monkeypatch, "TRUSTED_HOSTS", '["app.example.com"]')
        assert SecurityConfig().trusted_hosts == ["app.example.com"]

    def test_trusted_hosts_accepts_csv_and_blank(self, monkeypatch):
        from core.config.security import SecurityConfig

        _env(monkeypatch, "TRUSTED_HOSTS", "a.example,b.example")
        assert SecurityConfig().trusted_hosts == ["a.example", "b.example"]
        _env(monkeypatch, "TRUSTED_HOSTS", "")
        assert SecurityConfig().trusted_hosts == []

    def test_oidc_algorithms_accept_csv(self, monkeypatch):
        from core.config.security import SecurityConfig

        _env(monkeypatch, "OIDC_ALGORITHMS", "RS256,ES256")
        assert SecurityConfig().oidc_algorithms == ["RS256", "ES256"]

    def test_api_keys_accept_the_shipped_template_value(self, monkeypatch):
        """`.env.example:449` — ``API_KEYS_USER=__CHANGE_ME__``."""
        from core.config.security import SecurityConfig

        _env(monkeypatch, "API_KEYS_USER", "__CHANGE_ME__")
        keys = SecurityConfig().api_keys_user
        assert {k.get_secret_value() for k in keys} == {"__CHANGE_ME__"}

    def test_api_keys_accept_csv_and_stay_secret(self, monkeypatch):
        from core.config.security import SecurityConfig

        _env(monkeypatch, "API_KEYS_USER", f"{'a' * 40},{'b' * 40}")
        config = SecurityConfig()
        assert len(config.api_keys_user) == 2
        assert "a" * 40 not in repr(config)

    def test_api_keys_accept_the_json_array_form(self, monkeypatch):
        """Used by `tests/conftest.py`, the deployment docs and older
        deployments. NoDecode made pydantic stop decoding it, so the coercer
        had to start — until it did, every configured key silently stopped
        matching."""
        from core.config.security import SecurityConfig

        _env(monkeypatch, "API_KEYS_USER", f'["{"a" * 40}", "{"b" * 40}"]')
        keys = {k.get_secret_value() for k in SecurityConfig().api_keys_user}
        assert keys == {"a" * 40, "b" * 40}

    def test_oidc_algorithms_accept_the_json_array_form(self, monkeypatch):
        from core.config.security import SecurityConfig

        _env(monkeypatch, "OIDC_ALGORITHMS", '["RS256", "ES256"]')
        assert SecurityConfig().oidc_algorithms == ["RS256", "ES256"]

    def test_api_keys_accept_blank(self, monkeypatch):
        from core.config.security import SecurityConfig

        for name in ("API_KEYS_USER", "API_KEYS_ADMIN", "API_KEYS_JOB"):
            _env(monkeypatch, name, "")
        config = SecurityConfig()
        assert config.api_keys_user == set()
        assert config.api_keys_admin == set()
        assert config.api_keys_job == set()


class TestScraperConfig:
    def test_blocked_extensions_accept_csv(self, monkeypatch):
        from core.config.scraper import ScraperConfig

        _env(monkeypatch, "SCRAPER_BLOCKED_EXTENSIONS", ".exe,.zip")
        assert ScraperConfig().blocked_extensions == [".exe", ".zip"]

    def test_blocked_extensions_accept_blank(self, monkeypatch):
        from core.config.scraper import ScraperConfig

        _env(monkeypatch, "SCRAPER_BLOCKED_EXTENSIONS", "")
        assert ScraperConfig().blocked_extensions == []
