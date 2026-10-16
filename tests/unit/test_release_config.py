"""The semantic-release configuration decides every release type.

A commit is parsed twice on ``main``: commit-analyzer turns it into
major/minor/patch/none, release-notes-generator into the GitHub release
notes. Both read the parser options from the module ``.releaserc`` extends.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASERC = REPO_ROOT / ".releaserc"


def _releaserc() -> dict[str, Any]:
    return json.loads(RELEASERC.read_text(encoding="utf-8"))


def test_every_release_plugin_parses_with_the_shared_options() -> None:
    """The strict parser options reach both plugins that parse commits.

    They live in a module because the note pattern is a function, and
    ``extends`` turns them into top-level options, which semantic-release
    hands to every plugin. A plugin-level ``parserOpts`` would replace them
    wholesale for that plugin, so neither parsing plugin may carry one.
    """
    config = _releaserc()
    assert (REPO_ROOT / config["extends"]).is_file()
    for name in (
        "@semantic-release/commit-analyzer",
        "@semantic-release/release-notes-generator",
    ):
        for entry in config["plugins"]:
            if isinstance(entry, list) and entry[0] == name:
                assert "parserOpts" not in entry[1], name


_NOTE_PROBE = """
const { parserOpts } = await import(process.argv[1]);
const pattern = parserOpts.notesPattern("BREAKING CHANGE|BREAKING-CHANGE");
const lines = JSON.parse(process.argv[2]);
console.log(JSON.stringify({
  notes: lines.map((line) => pattern.test(line)),
  comment: parserOpts.commentChar,
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_only_a_real_footer_counts_as_a_breaking_change() -> None:
    """Prose that starts with the two words must not cut a major release.

    The parser's default note pattern is case-insensitive and takes a space
    for the colon, so a wrapped body line reading "BREAKING CHANGE note to
    the note's text" became a note, and the ``breaking`` rule made it major.
    """
    lines = {
        "BREAKING CHANGE: the old route is gone": True,
        "BREAKING-CHANGE: the old route is gone": True,
        "BREAKING CHANGE note to the note's text, so the notes": False,
        "  breaking-change rule has none. Skip it there": False,
        "breaking change: lower case is prose": False,
        "* BREAKING CHANGE: a bullet in the body": False,
    }
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            _NOTE_PROBE,
            str(REPO_ROOT / _releaserc()["extends"]),
            json.dumps(list(lines)),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    probe = json.loads(result.stdout)
    assert dict(zip(lines, probe["notes"], strict=True)) == lines
    # A trailer after a breaking footer is otherwise appended to the note.
    assert probe["comment"] == "Claude-Session:"
