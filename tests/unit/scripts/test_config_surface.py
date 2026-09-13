"""The configuration-surface gate reads sources instead of importing them.

Importing ``core.config`` loads the repository ``.env`` into ``os.environ``, so
an import-based reader would publish a developer's local overrides as the
shipped defaults. Everything is therefore parsed with ``ast``, which also gives
the reference page better labels than a runtime repr would.
"""

from pathlib import Path

import pytest

from scripts.config_surface import (
    Setting,
    env_example_entries,
    env_literals,
    iter_settings,
    render_reference,
)

SETTINGS_MODULE = '''"""Widget configuration."""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WidgetConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIDGET_", extra="ignore")

    # === Connection ===
    # Host the widget service answers on.
    host: str = Field(default="localhost")
    api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("WIDGET_API_KEY", "API_KEY"),
    )
    timeout: float = Field(default=1.5, description="Seconds before giving up.")
    tags: list[str] = Field(default_factory=list)
'''


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "core" / "config").mkdir(parents=True)
    (tmp_path / "core" / "config" / "widget.py").write_text(SETTINGS_MODULE)
    return tmp_path


class TestIterSettings:
    """Field declarations become one entry per binding."""

    def test_prefix_applies_when_no_alias_is_declared(self, repo):
        names = {setting.env for setting in iter_settings(repo)}

        assert "WIDGET_HOST" in names

    def test_alias_choices_keep_the_prefixed_name_first(self, repo):
        setting = next(s for s in iter_settings(repo) if s.env == "WIDGET_API_KEY")

        assert setting.alternatives == ("API_KEY",)
        assert setting.names == ("WIDGET_API_KEY", "API_KEY")

    def test_secret_fields_are_marked(self, repo):
        by_name = {s.env: s for s in iter_settings(repo)}

        assert by_name["WIDGET_API_KEY"].secret
        assert not by_name["WIDGET_HOST"].secret

    def test_defaults_and_factories_are_labelled(self, repo):
        by_name = {s.env: s for s in iter_settings(repo)}

        assert by_name["WIDGET_HOST"].default == "localhost"
        assert by_name["WIDGET_TAGS"].default == "computed"

    def test_description_falls_back_to_the_comment_above(self, repo):
        """More than half the fields document themselves in a plain comment."""
        by_name = {s.env: s for s in iter_settings(repo)}

        assert (
            by_name["WIDGET_HOST"].description == "Host the widget service answers on."
        )
        assert by_name["WIDGET_TIMEOUT"].description == "Seconds before giving up."

    def test_section_banners_are_not_read_as_descriptions(self, repo):
        """``# === Connection ===`` titles a block; it describes no field."""
        by_name = {s.env: s for s in iter_settings(repo)}

        assert "Connection" not in by_name["WIDGET_HOST"].description

    def test_section_title_comes_from_the_module_docstring(self, repo):
        assert {s.section for s in iter_settings(repo)} == {"Widget configuration"}


class TestEnvLiterals:
    """Source literals cover the variables read without a settings field."""

    def test_collects_upper_snake_tokens(self, repo):
        (repo / "core" / "reader.py").write_text('os.getenv("WIDGET_DEBUG")\n')

        assert "WIDGET_DEBUG" in env_literals(repo)

    def test_plugin_manifests_declare_bound_keys(self, repo):
        """A manifest key is injected by the loader with no Python literal."""
        manifest = repo / "plugins" / "widget"
        manifest.mkdir(parents=True)
        (manifest / "manifest.yaml").write_text("config:\n  WIDGET_MANIFEST_KEY: 1\n")

        assert "WIDGET_MANIFEST_KEY" in env_literals(repo)

    def test_the_checker_is_not_evidence_for_itself(self, repo):
        """Its own prose names variables; counting those would void the gate."""
        (repo / "scripts" / "config_surface").mkdir(parents=True)
        (repo / "scripts" / "config_surface" / "notes.py").write_text(
            '"""Mentions WIDGET_GHOST in prose."""\n'
        )

        assert "WIDGET_GHOST" not in env_literals(repo)


class TestEnvExampleEntries:
    """Both active and commented-out entries document a variable."""

    def test_reads_active_and_commented_entries_with_line_numbers(self, tmp_path):
        template = tmp_path / ".env.example"
        template.write_text("# header\nWIDGET_HOST=localhost\n# WIDGET_TIMEOUT=1.5\n")

        assert env_example_entries(template) == {"WIDGET_HOST": 2, "WIDGET_TIMEOUT": 3}


class TestRenderReference:
    """The page is deterministic and marks what matters."""

    def test_lists_every_setting_under_its_section(self, repo):
        page = render_reference(iter_settings(repo))

        assert "## Widget configuration" in page
        assert "`WIDGET_HOST`" in page
        assert "also accepts `API_KEY`" in page

    def test_marks_secrets_and_generated_provenance(self, repo):
        page = render_reference(iter_settings(repo))

        assert page.startswith("<!-- GENERATED")
        assert ":material-key:" in page

    def test_is_stable_across_runs(self, repo):
        assert render_reference(iter_settings(repo)) == render_reference(
            iter_settings(repo)
        )


def _setting(**overrides: object) -> Setting:
    fields: dict[str, object] = {
        "env": "WIDGET_HOST",
        "alternatives": (),
        "module": "core/config/widget.py",
        "section": "Widget configuration",
        "class_name": "WidgetSettings",
        "type_label": "str",
        "default": "localhost",
        "description": "",
        "secret": False,
    }
    fields.update(overrides)
    return Setting(**fields)  # type: ignore[arg-type]


class TestRenderSurvivesMarkdownlint:
    """The page passes the repo's markdownlint hook without being --fixed.

    Both hooks rewrite the same file, so anything markdownlint would repair
    here deadlocks the commit: markdownlint --fix rewrites the page, the next
    run of this generator rewrites it back, and neither ever settles.
    """

    def test_placeholder_defaults_use_asterisk_emphasis(self):
        page = render_reference(
            [_setting(default="computed"), _setting(env="WIDGET_PORT", default="")]
        )

        assert "*computed*" in page
        assert "*empty*" in page
        # MD049 runs in "consistent" mode and the preamble opens with
        # ``*required*``: an underscore span here is what markdownlint --fix
        # would rewrite.
        assert "_computed_" not in page
        assert "_empty_" not in page

    def test_html_in_a_docstring_is_escaped(self):
        page = render_reference(
            [_setting(description="Blocks no-cors loads (<img>, <script>).")]
        )

        # Raw <img> both vanished from the rendered page and tripped MD045,
        # which no --fix can repair.
        assert "&lt;img>" in page
        assert "(<img>" not in page

    def test_bare_urls_are_code_spanned(self):
        page = render_reference(
            [_setting(description="Falls back to http://localhost:11434.")]
        )

        # MD034, also unfixable by --fix; the trailing stop stays outside.
        assert "`http://localhost:11434`." in page


class TestEveryShippedTemplateIsChecked:
    """The "binds nothing" check must cover every template we tell people to copy.

    It only ever read ``.env.example``. But ``baselith doctor`` tells an operator
    to run ``cp configs/.env.base .env``, and the baselithbot docs call that file
    the reference template — so it is a template people actually start from, and
    it had rotted unobserved: ten entries naming a removed OCR backend and a
    widget API that no longer exists, none of which bind anything.
    """

    def test_env_base_is_in_the_checked_set(self):
        """The file `baselith doctor` recommends is one of the checked templates."""
        from scripts.check_config_surface import CHECKED_TEMPLATES

        names = {path.name for path in CHECKED_TEMPLATES}
        assert ".env.example" in names
        assert ".env.base" in names

    def test_a_template_entry_that_binds_nothing_is_reported(self, tmp_path):
        """The finding names the file, so an operator knows which to edit."""
        from scripts.check_config_surface import check_template

        template = tmp_path / ".env.base"
        template.write_text("REAL_SETTING=1\nGHOST_SETTING=2\n", encoding="utf-8")

        findings = check_template(template, {"REAL_SETTING"})

        assert len(findings) == 1
        assert ".env.base:2:" in findings[0]
        assert "GHOST_SETTING" in findings[0]

    def test_the_shipped_templates_are_actually_clean(self):
        """Regression guard on the real files, not a synthetic one."""
        from scripts.check_config_surface import (
            CHECKED_TEMPLATES,
            check_template,
            known_env_names,
        )

        known = known_env_names()
        findings = [
            f for path in CHECKED_TEMPLATES for f in check_template(path, known)
        ]

        assert findings == []
