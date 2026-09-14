"""The invariant behind the per-field tests: *every* collection setting parses.

``tests/unit/core/config/test_collection_env_parsing.py`` pins the specific
values the docs ship. This module pins the rule they are instances of, applied
to every sequence-of-scalars setting discovered at run time.

The distinction earned itself. Adding ``NoDecode`` to four security fields
stopped pydantic-settings decoding their JSON-array form, and the coercers
behind them only split on commas — so ``API_KEYS_USER=["k1","k2"]`` silently
became ``{'["k1"', '"k2"]'}`` and every configured key stopped matching. No
per-field test caught it, because the fields that broke were exactly the ones
nobody had written a JSON test for. Enumerating the fields removes that
correlation: a parser that gains ``NoDecode`` without a JSON branch now fails
here immediately.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]


def _collection_fields():
    """Every settings field that is a sequence of scalars, with its env name.

    Discovered rather than listed, so a field added later is covered without
    anyone remembering to add it here — which is the failure mode this gate
    exists to close. ``dict``-typed fields are excluded on purpose: their
    documented env spelling is ``k:v,k:v`` or a nested JSON object, not a
    comma-separated list, so the invariant below does not apply to them.
    """
    import importlib
    import inspect
    import pkgutil
    import typing

    from pydantic_settings import BaseSettings

    import core.config

    found = []
    for mod in pkgutil.iter_modules(core.config.__path__):
        module = importlib.import_module(f"core.config.{mod.name}")
        for _name, obj in vars(module).items():
            if not (
                inspect.isclass(obj)
                and issubclass(obj, BaseSettings)
                and obj is not BaseSettings
                and obj.__module__ == module.__name__
            ):
                continue
            prefix = obj.model_config.get("env_prefix", "") or ""
            for field_name, field in obj.model_fields.items():
                if typing.get_origin(field.annotation) not in (
                    list,
                    set,
                    tuple,
                    frozenset,
                ):
                    continue
                args = typing.get_args(field.annotation)
                if not args or getattr(args[0], "__name__", "") not in (
                    "str",
                    "SecretStr",
                ):
                    continue
                alias = field.alias or (
                    field.validation_alias
                    if isinstance(field.validation_alias, str)
                    else None
                )
                env = alias or f"{prefix}{field_name}"
                found.append(pytest.param(obj, field_name, env.upper(), id=env.upper()))
    return found


def _values(parsed) -> set[str]:
    """Comparable form of a parsed collection, secrets unwrapped."""
    out = set()
    for item in parsed:
        text = item.get_secret_value() if hasattr(item, "get_secret_value") else item
        # documents_extensions normalises a leading dot; compare past it.
        out.add(str(text).lstrip(".").lower())
    return out


class TestEveryCollectionFieldAcceptsEveryDocumentedForm:
    """The invariant, applied to every sequence-of-scalars setting there is.

    Three spellings reach these fields in the wild — comma-separated (what the
    docs mostly show), blank (what a copied template leaves behind), and a JSON
    array (what pydantic-settings required before ``NoDecode``, so it is what
    older deployments, `.env.example:448` and `tests/conftest.py` still use).
    All three must parse, and none may leak JSON punctuation into the values.

    A per-field test would have missed the ``NoDecode``-without-a-JSON-branch
    regression, because the fields that broke were exactly the ones nobody had
    written a JSON test for. Enumerating the fields removes that correlation.
    """

    @pytest.fixture(autouse=True)
    def _baseline_env(self, monkeypatch):
        """Satisfy cross-field checks unrelated to collection parsing."""
        monkeypatch.setenv("SECRET_KEY", "s" * 48)
        monkeypatch.setenv("AUTH_REQUIRED", "false")

    @pytest.mark.parametrize(("config_cls", "field_name", "env"), _collection_fields())
    def test_comma_separated(self, monkeypatch, config_cls, field_name, env):
        monkeypatch.setenv(env, "alpha,beta")
        assert _values(getattr(config_cls(), field_name)) == {"alpha", "beta"}

    @pytest.mark.parametrize(("config_cls", "field_name", "env"), _collection_fields())
    def test_blank(self, monkeypatch, config_cls, field_name, env):
        monkeypatch.setenv(env, "")
        assert list(getattr(config_cls(), field_name)) == []

    @pytest.mark.parametrize(("config_cls", "field_name", "env"), _collection_fields())
    def test_json_array(self, monkeypatch, config_cls, field_name, env):
        """The regression this gate exists for: ``NoDecode`` stops pydantic
        decoding a JSON array, so every parser behind it must decode one."""
        monkeypatch.setenv(env, '["alpha", "beta"]')
        parsed = _values(getattr(config_cls(), field_name))
        assert parsed == {"alpha", "beta"}, (
            f"{env} mangled a JSON array into {parsed!r} — its parser has no "
            "JSON branch behind NoDecode"
        )

    @pytest.mark.parametrize(("config_cls", "field_name", "env"), _collection_fields())
    def test_no_json_punctuation_survives(
        self, monkeypatch, config_cls, field_name, env
    ):
        """Pins the *shape* of the failure, not just one value."""
        monkeypatch.setenv(env, '["alpha", "beta"]')
        for value in _values(getattr(config_cls(), field_name)):
            assert not set(value) & set('[]"'), f"{env} leaked JSON syntax: {value!r}"

    def test_the_discovery_actually_found_the_fields(self):
        """Guard against a silently empty parametrisation."""
        names = {param.values[2] for param in _collection_fields()}
        assert {
            "API_KEYS_USER",
            "OIDC_ALGORITHMS",
            "ALLOW_ORIGINS",
            "TASK_QUEUE_QUEUES",
            "DOCUMENTS_EXTENSIONS",
            "METRICS_TENANT_LABEL_ALLOWLIST",
        } <= names
        assert len(names) >= 15


class TestEnvExampleTemplateLoads:
    """The end-to-end promise: copying `.env.example` must not break a class."""

    def test_every_settings_class_builds_from_the_shipped_template(
        self, monkeypatch, tmp_path
    ):
        import importlib
        import inspect
        import pkgutil
        from pathlib import Path

        from pydantic_settings import BaseSettings

        import core.config

        repo_root = Path(__file__).resolve().parents[4]
        for raw in (repo_root / ".env.example").read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key.replace("_", "").isalnum():
                monkeypatch.setenv(key, value.strip())

        failures = []
        for mod in pkgutil.iter_modules(core.config.__path__):
            module = importlib.import_module(f"core.config.{mod.name}")
            for name, obj in vars(module).items():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseSettings)
                    and obj is not BaseSettings
                    and obj.__module__ == module.__name__
                ):
                    try:
                        obj()
                    except Exception as exc:
                        failures.append(
                            f"{module.__name__}.{name}: {type(exc).__name__}"
                        )
        assert failures == [], "\n".join(sorted(set(failures)))
