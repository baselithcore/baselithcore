"""The parent side of a file run: it re-validates whatever comes back.

The bootstrap shares its process with the model's code, so the archive after
the boundary is untrusted — these tests forge it directly.
"""

from __future__ import annotations

import base64
import io
import json
import tarfile

import pytest

from baselith.core.services.sandbox.bootstrap import (
    MAX_REFUSALS,
    REFUSALS_MEMBER,
    USER_STDOUT_CAP,
    display_name,
    encoded_output_cap,
    is_flat_name,
    unpack_outputs,
)

BOUNDARY = "fedcba9876543210fedcba9876543210"
MISSING = {"*": "archive_missing"}


def _regular(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    return info, content


def _special(name: str, kind: bytes) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    if kind == tarfile.SYMTYPE:
        info.linkname = "/etc/passwd"
    return info, None


def _archive(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for info, content in members:
            tar.addfile(info, io.BytesIO(content) if content is not None else None)
    return buf.getvalue()


def _stdout(raw: bytes, user: bytes = b"user output\n") -> bytes:
    return user + b"\n" + BOUNDARY.encode() + b"\n" + base64.b64encode(raw) + b"\n"


def _unpack(
    stdout: bytes, *, max_files: int = 16, max_bytes: int = 1 << 20
) -> tuple[str, dict[str, bytes], dict[str, str]]:
    return unpack_outputs(stdout, BOUNDARY, max_files=max_files, max_bytes=max_bytes)


def test_unpack_revalidates_a_forged_archive() -> None:
    members = [_regular(f"f{i:02d}.txt", b"data") for i in range(20)]
    members += [
        _special("link.txt", tarfile.SYMTYPE),
        _special("dir", tarfile.DIRTYPE),
        _special("fifo", tarfile.FIFOTYPE),
        _regular("../x", b"escape"),
        _regular("/etc/passwd", b"absolute"),
        _regular("a/b.txt", b"nested"),
    ]
    stdout, files, refusals = _unpack(_stdout(_archive(members)), max_files=16)

    assert stdout == "user output"
    assert sorted(files) == [f"f{i:02d}.txt" for i in range(16)]
    assert refusals == {
        "f16.txt": "too_many",
        "f17.txt": "too_many",
        "f18.txt": "too_many",
        "f19.txt": "too_many",
        "link.txt": "not_a_file",
        "dir": "not_a_file",
        "fifo": "not_a_file",
        "..?x": "bad_name",
        "?etc?passwd": "bad_name",
        "a?b.txt": "bad_name",
    }


def test_unpack_keeps_the_first_of_two_members_with_one_name() -> None:
    members = [_regular("a.txt", b"first"), _regular("a.txt", b"second")]
    _, files, _ = _unpack(_stdout(_archive(members)))
    assert files == {"a.txt": b"first"}


def test_unpack_enforces_a_running_byte_budget() -> None:
    members = [
        _regular("a.bin", b"x" * 60),
        _regular("b.bin", b"x" * 60),
        _regular("c.bin", b"x" * 10),
    ]
    _, files, refusals = _unpack(_stdout(_archive(members)), max_bytes=100)
    assert sorted(files) == ["a.bin", "c.bin"]
    assert refusals == {"b.bin": "too_large"}


def test_unpack_takes_only_known_reasons_from_the_manifest() -> None:
    manifest = json.dumps(
        {"x.txt": "too_many", "y.txt": "rm -rf /", "bad\nname": "bad_name", "z": 5}
    ).encode()
    members = [_regular(REFUSALS_MEMBER, manifest), _regular("ok.txt", b"ok")]
    _, files, refusals = _unpack(_stdout(_archive(members)))
    assert files == {"ok.txt": b"ok"}
    assert refusals == {"x.txt": "too_many", "bad?name": "bad_name"}


@pytest.mark.parametrize("manifest", [b"not json", b"[1, 2]", b"[" * 5000])
def test_unpack_ignores_a_malformed_manifest(manifest: bytes) -> None:
    members = [_regular(REFUSALS_MEMBER, manifest), _regular("ok.txt", b"ok")]
    _, files, refusals = _unpack(_stdout(_archive(members)))
    assert files == {"ok.txt": b"ok"}
    assert refusals == {}


def test_unpack_caps_the_refusals() -> None:
    members = [_special(f"link{i:03d}", tarfile.SYMTYPE) for i in range(200)]
    _, files, refusals = _unpack(_stdout(_archive(members)))
    assert files == {}
    assert len(refusals) == MAX_REFUSALS


def test_unpack_rejects_corrupt_archives() -> None:
    good = _archive([_regular("a.txt", b"x" * 4096)])
    marker = b"\n" + BOUNDARY.encode() + b"\n"
    for section in (
        b"!!! not base64 !!!",
        base64.b64encode(b"definitely not a tar archive" * 40),
        base64.b64encode(good[:1024]),  # cut inside the member's data
        base64.b64encode(good) + b"!",  # the bootstrap's "archive failed" mark
    ):
        stdout, files, refusals = _unpack(b"hi" + marker + section)
        assert (stdout, files, refusals) == ("hi", {}, MISSING)


def test_unpack_refuses_an_archive_larger_than_the_limits_allow() -> None:
    raw = _archive([_regular("a.bin", b"x" * 50_000)])
    stdout = _stdout(raw)
    assert len(base64.b64encode(raw)) > encoded_output_cap(1, 100)
    assert _unpack(stdout, max_files=1, max_bytes=100) == ("user output", {}, MISSING)


def test_user_stdout_is_capped_before_decoding() -> None:
    stdout, _, _ = _unpack(_stdout(_archive([]), user=b"x" * (3 << 20)))
    assert len(stdout) < USER_STDOUT_CAP + 100
    assert stdout.endswith("[... stdout truncated ...]")

    raw, files, refusals = unpack_outputs(
        b"y" * (3 << 20), BOUNDARY, max_files=1, max_bytes=1
    )
    assert len(raw) < USER_STDOUT_CAP + 100
    assert (files, refusals) == ({}, MISSING)


def test_no_boundary_means_no_outputs_and_says_so() -> None:
    for boundary in ("", BOUNDARY):
        result = unpack_outputs(b"hello\n", boundary, max_files=1, max_bytes=1)
        assert result == ("hello", {}, MISSING)


def test_only_the_manifest_may_flag_a_truncated_listing() -> None:
    manifest = json.dumps({"*": "listing_truncated"}).encode()
    members = [_regular(REFUSALS_MEMBER, manifest), _regular("*", b"star")]
    _, files, refusals = _unpack(_stdout(_archive(members)))
    assert files == {}
    assert refusals == {"*": "listing_truncated", "?": "bad_name"}

    for forged in ({"*": "archive_missing"}, {"*": "too_many"}):
        members = [_regular(REFUSALS_MEMBER, json.dumps(forged).encode())]
        assert _unpack(_stdout(_archive(members)))[2] == {}


@pytest.mark.parametrize(
    ("name", "flat"),
    [
        ("report.xlsx", True),
        ("my file - v2.csv", True),
        (".hidden", True),
        ("x" * 120, True),
        ("x" * 121, False),
        ("", False),
        (".", False),
        ("..", False),
        ("..x", False),
        ("a/b", False),
        ("a\\b", False),
        ("café.txt", False),
        ("new\nline", False),
    ],
)
def test_the_flat_name_rule(name: str, flat: bool) -> None:
    assert is_flat_name(name) is flat


def test_display_name_is_safe_and_idempotent() -> None:
    assert display_name("bad\nname.txt") == "bad?name.txt"
    assert display_name("x" * 200) == "x" * 119 + "?"
    assert display_name("ok.txt") == "ok.txt"
    for name in ("bad\nname.txt", "x" * 200, "../../etc", "café"):
        once = display_name(name)
        assert display_name(once) == once
        assert len(once) <= 120
