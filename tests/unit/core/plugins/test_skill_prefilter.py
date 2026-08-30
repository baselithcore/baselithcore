"""Tests for the skill catalog relevance pre-filter (``render_catalog`` query)."""

from __future__ import annotations

from pathlib import Path

from core.plugins.skills_service import (
    PREFILTER_THRESHOLD_ENV,
    PREFILTER_TOP_K_ENV,
    SKILL_CATALOG_PREFILTER_THRESHOLD,
    SKILL_CATALOG_PREFILTER_TOP_K,
    SkillService,
)


def _write_skill(root: Path, slug: str, *, description: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: {description}\n---\n\nBody.\n",
        encoding="utf-8",
    )


class _FakeRegistry:
    """Minimal stand-in exposing the registry's skill-root lookup."""

    def __init__(self, roots: dict[str, Path]) -> None:
        self._roots = roots

    def get_all_skill_roots(self) -> dict[str, Path]:
        return {name: root for name, root in self._roots.items() if root.is_dir()}


def _service(
    tmp_path: Path, count: int, *, extra: dict[str, str] | None = None
) -> SkillService:
    root = tmp_path / "plug" / "skills"
    root.mkdir(parents=True)
    for i in range(count):
        _write_skill(
            root,
            f"generic-{i:02d}",
            description=f"Housekeeping chore variant number {i}.",
        )
    for slug, description in (extra or {}).items():
        _write_skill(root, slug, description=description)
    return SkillService(_FakeRegistry({"plug": root}))  # type: ignore[arg-type]


def _card_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("- ")]


class TestDefaults:
    def test_module_defaults(self) -> None:
        assert SKILL_CATALOG_PREFILTER_THRESHOLD == 50
        assert SKILL_CATALOG_PREFILTER_TOP_K == 25

    def test_env_names(self) -> None:
        assert PREFILTER_THRESHOLD_ENV == "BASELITH_SKILL_CATALOG_PREFILTER_THRESHOLD"
        assert PREFILTER_TOP_K_ENV == "BASELITH_SKILL_CATALOG_PREFILTER_TOP_K"


class TestUnderThreshold:
    def test_query_output_identical_to_no_query(self, tmp_path: Path) -> None:
        service = _service(tmp_path, 3)
        assert (
            service.render_catalog(query="housekeeping chore")
            == service.render_catalog()
        )


class TestOverThreshold:
    def test_no_query_passthrough_lists_everything(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(PREFILTER_THRESHOLD_ENV, "5")
        service = _service(tmp_path, 8)
        text = service.render_catalog()
        assert len(_card_lines(text)) == 8
        assert "filtered" not in text.lower()

    def test_blank_query_passthrough(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv(PREFILTER_THRESHOLD_ENV, "5")
        service = _service(tmp_path, 8)
        assert service.render_catalog(query="   ") == service.render_catalog()

    def test_query_filters_to_top_k_with_note(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(PREFILTER_THRESHOLD_ENV, "5")
        monkeypatch.setenv(PREFILTER_TOP_K_ENV, "3")
        service = _service(
            tmp_path,
            8,
            extra={
                "database-migrate": "Migrate the production database schema safely."
            },
        )
        text = service.render_catalog(query="migrate the database schema")
        lines = _card_lines(text)
        assert len(lines) == 3
        # The BM25 match ranks first, ahead of the alphabetical filler.
        assert lines[0].startswith("- database-migrate")
        assert "filtered" in text.lower()
        assert "3" in text and "9" in text  # shown-of-total counts in the note

    def test_query_without_matches_pads_deterministically(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(PREFILTER_THRESHOLD_ENV, "5")
        monkeypatch.setenv(PREFILTER_TOP_K_ENV, "4")
        service = _service(tmp_path, 8)
        text = service.render_catalog(query="zzz qqq nomatch")
        lines = _card_lines(text)
        assert len(lines) == 4
        assert lines[0].startswith("- generic-00")  # alphabetical fallback

    def test_invalid_env_falls_back_to_default(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(PREFILTER_THRESHOLD_ENV, "not-a-number")
        service = _service(tmp_path, 8)  # 8 < default threshold of 50
        assert service.render_catalog(query="housekeeping") == service.render_catalog()
