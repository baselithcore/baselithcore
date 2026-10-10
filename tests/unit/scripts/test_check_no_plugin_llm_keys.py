"""scripts/check_no_plugin_llm_keys.py: flags provider clients, hosts and key
resolvers in plugins, honours a shrink-only allowlist, and the repo is clean."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_no_plugin_llm_keys",
    Path(__file__).resolve().parents[3] / "scripts" / "check_no_plugin_llm_keys.py",
)
assert _SPEC and _SPEC.loader
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard  # dataclasses resolve their module by name
_SPEC.loader.exec_module(guard)

NONE = "none.yaml"


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _allow(root: Path, *entries: tuple[str, str]) -> Path:
    lines = ["allow:"] + [f"  - path: {p}\n    reason: {r}" for p, r in entries]
    path = root / "allow.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "source",
    [
        "import openai\n",
        "import anthropic\n",
        "import ollama\n",
        "import openai.types\n",
        "from openai import AsyncOpenAI\n",
        "from anthropic import Anthropic\n",
        "from ollama import ResponseError\n",
        "def f():\n    import ollama\n",  # lazy import inside a function
        "URL = 'https://api.openai.com/v1/chat/completions'\n",
        "URL = 'https://api.anthropic.com/v1/messages'\n",
        "from baselith.core.services.vision.service import register_api_key_resolver\n",
        "register_api_key_resolver(lambda p: None)\n",
        "svc.register_api_key_resolver(fn)\n",
    ],
)
def test_each_forbidden_construct_is_flagged(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "baselith/plugins/p/mod.py", source)
    problems = guard.check(tmp_path, tmp_path / NONE)
    assert problems and all("baselith/plugins/p/mod.py" in line for line in problems)


def test_prose_tests_build_dirs_and_other_modules_are_ignored(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "baselith/plugins/p/a.py",
        '"""Talks to api.openai.com via import openai in prose."""\n'
        "def f():\n"
        '    """Calls api.anthropic.com."""\n'
        "# import openai\n"
        "from baselith.core.services.llm import get_llm_service\n"
        "import openai_compat\n"
        "from myopenai import x\n",
    )
    _write(tmp_path, "baselith/plugins/p/tests/test_a.py", "import openai\n")
    _write(tmp_path, "baselith/plugins/p/ui/node_modules/m.py", "import anthropic\n")
    _write(tmp_path, "baselith/core/x.py", "import openai\n")  # core is not scanned
    assert guard.check(tmp_path, tmp_path / NONE) == []


def test_a_relative_import_named_like_a_provider_is_not_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "baselith/plugins/p/a.py", "from .openai import thing\n")
    assert guard.check(tmp_path, tmp_path / NONE) == []


def test_the_message_points_at_the_funnel(tmp_path: Path) -> None:
    _write(tmp_path, "baselith/plugins/p/a.py", "import openai\n")
    assert "get_llm_service" in guard.check(tmp_path, tmp_path / NONE)[0]


def test_allowlist_exempts_with_a_reason_and_goes_stale(tmp_path: Path) -> None:
    _write(tmp_path, "baselith/plugins/v/vendor/e.py", "import openai\n")
    allow = _allow(tmp_path, ("baselith/plugins/v/vendor", "vendored, not migrated"))
    assert guard.check(tmp_path, allow) == []

    _write(
        tmp_path, "baselith/plugins/v/vendor/e.py", "x = 1\n"
    )  # migrated, entry now stale
    assert "no longer contains a flagged construct" in guard.check(tmp_path, allow)[0]

    (tmp_path / "baselith/plugins/v/vendor/e.py").unlink()
    (tmp_path / "baselith/plugins/v/vendor").rmdir()
    assert "no longer exists" in guard.check(tmp_path, allow)[0]


def test_an_allowlist_prefix_does_not_exempt_a_sibling(tmp_path: Path) -> None:
    _write(tmp_path, "baselith/plugins/v/vendor/e.py", "import openai\n")
    _write(tmp_path, "baselith/plugins/v/vendor_extra/e.py", "import openai\n")
    allow = _allow(tmp_path, ("baselith/plugins/v/vendor", "vendored"))
    problems = guard.check(tmp_path, allow)
    assert len(problems) == 1 and "vendor_extra" in problems[0]


def test_an_allowlist_entry_without_a_reason_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "allow.yaml"
    path.write_text(
        "allow:\n  - path: baselith/plugins/p\n    reason: ''\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="reason"):
        guard.load_allowlist(path)


def test_the_repository_is_clean_against_the_checked_in_allowlist() -> None:
    assert guard.check() == []
