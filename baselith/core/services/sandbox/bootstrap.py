"""The trusted bootstrap that moves files into and out of a sandbox run.

The hardened container has a read-only root filesystem and a ``/tmp`` tmpfs,
so neither the Docker archive API nor ``docker cp`` can reach it. Files travel
through the container's own stdin and stdout instead:

1. the parent sends an 8-byte big-endian length, then an uncompressed tar whose
   first member is the code (:data:`CODE_MEMBER`) and whose other members are
   the inputs (:func:`pack_inputs`);
2. :data:`BOOTSTRAP` — fixed source, part of core and never model-written, run
   as ``python -c BOOTSTRAP`` — extracts regular, flat-named files into
   ``/tmp/in``, writes the code to ``/tmp/run/main.py`` and runs it in a
   **child** interpreter from ``/tmp/out`` (stdin closed, stdout and stderr
   inherited), so the child's normal shutdown — flushing unclosed files,
   joining non-daemon threads, ``atexit`` — finishes before anything is
   collected. Past :data:`ENV_DEADLINE` seconds the child is killed (exit
   124). Whatever is left of its process tree is killed too, then the
   bootstrap writes ``\\n<boundary>\\n`` and the base64 of a bounded tar of
   ``/tmp/out``;
3. the parent splits stdout at the **last** boundary and re-validates every
   member of that tar (:func:`unpack_outputs`).

The model's code runs as the same uid in the same container: it decides what
lands in ``/tmp/out``, can read the boundary from ``/proc`` and print a forged
archive before the real one, and on a host that allows ``ptrace`` of a parent
could even tamper with the bootstrap. The boundary is a framing device, not a
secret. The container is the security boundary; the parent's re-validation
(names, types, counts, sizes, an in-memory parse with no extraction) is what
keeps a forged archive from being worth more than files written to
``/tmp/out`` honestly.
"""

from __future__ import annotations

import binascii
import io
import json
import re
import struct
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .bootstrap_source import SOURCE_TEMPLATE

#: Flat file names: no separators, no leading ``..``, at most 120 characters.
NAME_PATTERN = r"[A-Za-z0-9._ -]{1,120}"
#: Tar member that carries the code to run (never written to ``/tmp/in``).
CODE_MEMBER = "__code__.py"
#: Tar member listing the bootstrap's refusals; the ``/`` keeps it from ever
#: colliding with a flat output name.
REFUSALS_MEMBER = "baselith-meta/refusals.json"
#: Inputs per run.
MAX_INPUT_FILES = 64
#: Input bytes the bootstrap accepts when the parent does not say (64 MiB /tmp).
DEFAULT_MAX_INPUT_BYTES = 64 * 1024 * 1024
#: Encoded size of the code (it no longer rides in argv, so 128 KiB is not a limit).
MAX_CODE_BYTES = 1024 * 1024
DEFAULT_MAX_OUTPUT_FILES = 16
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
#: Upper bounds a caller may ask for (the parent buffers the encoded archive).
MAX_OUTPUT_FILES_CEILING = 256
MAX_OUTPUT_BYTES_CEILING = 1024 * 1024 * 1024
#: Refusals reported per run; more are dropped.
MAX_REFUSALS = 64
#: Bytes of the code's stdout kept before decoding.
USER_STDOUT_CAP = 1024 * 1024
#: Environment variables the bootstrap reads (callers may not set them).
ENV_PREFIX = "BASELITH_SANDBOX_"
ENV_BOUNDARY = "BASELITH_SANDBOX_BOUNDARY"
ENV_MAX_INPUT_BYTES = "BASELITH_SANDBOX_MAX_INPUT_BYTES"
ENV_MAX_OUTPUT_FILES = "BASELITH_SANDBOX_MAX_OUTPUT_FILES"
ENV_MAX_OUTPUT_BYTES = "BASELITH_SANDBOX_MAX_OUTPUT_BYTES"
#: Seconds the bootstrap gives the child before killing it (see
#: :func:`bootstrap_deadline`).
ENV_DEADLINE = "BASELITH_SANDBOX_DEADLINE"
REFUSAL_REASONS = frozenset({"too_many", "too_large", "bad_name", "not_a_file"})
#: Refusal key for the collection as a whole, never a file name (``*`` is not
#: flat): ``listing_truncated`` (more entries in ``/tmp/out`` than the
#: bootstrap scans) or ``archive_missing`` (no usable archive arrived).
COLLECTION_KEY = "*"
LISTING_TRUNCATED = "listing_truncated"
ARCHIVE_MISSING = "archive_missing"

_NAME = re.compile(NAME_PATTERN)
_BAD_CHAR = re.compile(r"[^A-Za-z0-9._ -]")
_MAX_MANIFEST_BYTES = 16 * 1024
_MEMBER_OVERHEAD = 2048  # ustar header + pax header and record + data padding
_TAR_TRAILER = 20480  # end-of-archive blocks + record padding
_SCAN_LIMIT = 4096  # entries of /tmp/out the bootstrap looks at
_TRUNCATED = "\n[... stdout truncated ...]"


_TOKENS = {
    "@NAME@": NAME_PATTERN,
    "@CODE@": CODE_MEMBER,
    "@META@": REFUSALS_MEMBER,
    "@CODE_CAP@": str(MAX_CODE_BYTES),
    "@MAX_IN@": str(MAX_INPUT_FILES),
    "@OVERHEAD@": str(_MEMBER_OVERHEAD),
    "@TRAILER@": str(_TAR_TRAILER),
    "@SCAN@": str(_SCAN_LIMIT),
    "@MAX_REFUSALS@": str(MAX_REFUSALS),
    "@ENV_BOUNDARY@": ENV_BOUNDARY,
    "@ENV_IN_BYTES@": ENV_MAX_INPUT_BYTES,
    "@ENV_OUT_FILES@": ENV_MAX_OUTPUT_FILES,
    "@ENV_OUT_BYTES@": ENV_MAX_OUTPUT_BYTES,
    "@ENV_DEADLINE@": ENV_DEADLINE,
    "@ENV_PREFIX@": ENV_PREFIX,
    "@DEFAULT_IN_BYTES@": str(DEFAULT_MAX_INPUT_BYTES),
    "@DEFAULT_OUT_FILES@": str(DEFAULT_MAX_OUTPUT_FILES),
    "@DEFAULT_OUT_BYTES@": str(DEFAULT_MAX_OUTPUT_BYTES),
}


def _render(source: str) -> str:
    for token, value in _TOKENS.items():
        source = source.replace(token, value)
    return source


#: The fixed bootstrap source, run in the container as ``python -c BOOTSTRAP``.
BOOTSTRAP: str = _render(SOURCE_TEMPLATE)


def is_flat_name(name: str) -> bool:
    """Return whether ``name`` is a flat file name a run may take or return."""
    return bool(_NAME.fullmatch(name)) and name != "." and not name.startswith("..")


def display_name(name: str) -> str:
    """Return a printable stand-in for an untrusted name (idempotent, <= 120 chars)."""
    shown = _BAD_CHAR.sub("?", name)
    return shown if len(shown) <= 120 else shown[:119] + "?"


def _check_inputs(inputs: Mapping[str, bytes]) -> None:
    if len(inputs) > MAX_INPUT_FILES:
        raise ValueError(f"a file run takes at most {MAX_INPUT_FILES} inputs")
    for name, data in inputs.items():
        if not isinstance(name, str) or not is_flat_name(name) or name == CODE_MEMBER:
            raise ValueError(
                f"input name {display_name(str(name))!r} is not a flat file name"
            )
        if not isinstance(data, bytes):
            raise ValueError(f"input {name!r} must be bytes")


@dataclass(frozen=True)
class FileTransfer:
    """Files to place in a run's ``/tmp/in``, and bounds on what comes back.

    Attributes:
        inputs: File name -> content. Names are flat (``[A-Za-z0-9._ -]{1,120}``,
            no leading ``..``); at most :data:`MAX_INPUT_FILES` files.
        max_output_files: Files of ``/tmp/out`` returned at most.
        max_output_bytes: Total bytes of ``/tmp/out`` returned at most.

    Raises:
        ValueError: A bad name, a non-bytes value, too many inputs or a limit
            outside ``0..MAX_OUTPUT_*_CEILING``.
    """

    inputs: Mapping[str, bytes]
    max_output_files: int = DEFAULT_MAX_OUTPUT_FILES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES

    def __post_init__(self) -> None:
        _check_inputs(self.inputs)
        if not 0 <= self.max_output_files <= MAX_OUTPUT_FILES_CEILING:
            raise ValueError("max_output_files is out of range")
        if not 0 <= self.max_output_bytes <= MAX_OUTPUT_BYTES_CEILING:
            raise ValueError("max_output_bytes is out of range")


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), 0o600, 0
    tar.addfile(info, io.BytesIO(data))


def pack_inputs(inputs: Mapping[str, bytes], code: str) -> bytes:
    """Build the stdin payload: an 8-byte big-endian length, then the tar.

    Args:
        inputs: File name -> content, validated like :class:`FileTransfer`.
        code: The Python source to run; becomes the :data:`CODE_MEMBER` member.

    Returns:
        The framed, uncompressed tar the bootstrap reads from stdin.

    Raises:
        ValueError: A bad input or code over :data:`MAX_CODE_BYTES`.
    """
    _check_inputs(inputs)
    source = code.encode("utf-8")
    if len(source) > MAX_CODE_BYTES:
        raise ValueError(f"the code is larger than {MAX_CODE_BYTES} bytes")
    buf = io.BytesIO()
    buf.write(b"\0" * 8)
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        _add(tar, CODE_MEMBER, source)
        for name, data in inputs.items():
            _add(tar, name, data)
    buf.getbuffer()[:8] = struct.pack(">Q", buf.tell() - 8)
    return buf.getvalue()


def bootstrap_deadline(timeout: float) -> float:
    """Return the seconds the bootstrap lets the code run within ``timeout``.

    It keeps ``max(2 s, 10 %)`` of the parent's timeout for collecting and
    sending the outputs, so a slow run ends with exit 124 *and* its archive
    instead of being killed from outside with nothing.
    """
    return max(timeout - max(2.0, timeout * 0.1), 0.5)


def encoded_output_cap(max_files: int, max_bytes: int) -> int:
    """Return the most base64 a bootstrap honouring these limits can emit."""
    tar_cap = (
        max_bytes
        + (max_files + 1) * _MEMBER_OVERHEAD
        + _MAX_MANIFEST_BYTES
        + _TAR_TRAILER
    )
    return (tar_cap + 2) // 3 * 4 + 4


def decode_stdout(data: bytes) -> str:
    """Decode the code's stdout, keeping at most :data:`USER_STDOUT_CAP` bytes."""
    text = data[:USER_STDOUT_CAP].decode("utf-8", errors="replace").strip()
    return text + _TRUNCATED if len(data) > USER_STDOUT_CAP else text


def _merge_manifest(
    tar: tarfile.TarFile, member: tarfile.TarInfo, refuse: Callable[[str, str], None]
) -> None:
    handle = tar.extractfile(member) if member.isreg() else None
    if handle is None or member.size > _MAX_MANIFEST_BYTES:
        return
    try:
        listed = json.loads(handle.read())
    except (ValueError, RecursionError):
        return
    if isinstance(listed, dict):
        for name, reason in listed.items():
            if name == COLLECTION_KEY:
                if reason == LISTING_TRUNCATED:
                    refuse(COLLECTION_KEY, reason)
            elif isinstance(reason, str) and reason in REFUSAL_REASONS:
                refuse(display_name(name), reason)


def _read_archive(
    raw: bytes, max_files: int, max_bytes: int
) -> tuple[dict[str, bytes], dict[str, str]]:
    files: dict[str, bytes] = {}
    refusals: dict[str, str] = {}

    def refuse(key: str, reason: str) -> None:
        if len(refusals) < MAX_REFUSALS:
            refusals.setdefault(key, reason)

    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for _ in range(max_files + MAX_REFUSALS + 1):
            member = tar.next()
            if member is None:
                break
            name = member.name
            if name == REFUSALS_MEMBER:
                _merge_manifest(tar, member, refuse)
            elif not member.isreg() or member.issparse():
                refuse(display_name(name), "not_a_file")
            elif not is_flat_name(name) or name in files:
                refuse(display_name(name), "bad_name")
            elif len(files) >= max_files:
                refuse(name, "too_many")
            elif member.size > max_bytes - total:
                refuse(name, "too_large")
            else:
                handle = tar.extractfile(member)
                data = handle.read() if handle is not None else b""
                if len(data) != member.size:
                    raise tarfile.ReadError("a member is cut short")
                files[name] = data
                total += len(data)
    return files, refusals


def unpack_outputs(
    stdout: bytes, boundary: str, *, max_files: int, max_bytes: int
) -> tuple[str, dict[str, bytes], dict[str, str]]:
    """Split a file run's stdout into the code's output and its files.

    Takes the **last** ``\\n<boundary>\\n``: everything before it is the code's
    stdout (capped at :data:`USER_STDOUT_CAP` before decoding), everything after
    it the base64 tar. The tar is parsed in memory and every member is checked
    again — flat name, regular file, ``max_files``, running ``max_bytes`` —
    whatever the bootstrap claims. A missing boundary, an oversized or corrupt
    archive yields no files and the single refusal
    ``{COLLECTION_KEY: ARCHIVE_MISSING}``.

    Args:
        stdout: The container's raw stdout.
        boundary: The run's random boundary.
        max_files: Files returned at most.
        max_bytes: Total bytes returned at most.

    Returns:
        ``(user stdout, files, refusals)``; refusals map a display name to
        ``too_many``, ``too_large``, ``bad_name`` or ``not_a_file``.
    """
    missing = {COLLECTION_KEY: ARCHIVE_MISSING}
    marker = f"\n{boundary}\n".encode("ascii", errors="replace") if boundary else b""
    cut = stdout.rfind(marker) if marker else -1
    if cut < 0:
        return decode_stdout(stdout), {}, missing
    user = decode_stdout(stdout[:cut])
    start, end = cut + len(marker), len(stdout)
    while end > start and stdout[end - 1] in b" \t\r\n":
        end -= 1
    if end - start > encoded_output_cap(max_files, max_bytes):
        return user, {}, missing
    try:
        # a2b_base64 on a memoryview: no copy of the (large) encoded section.
        raw = binascii.a2b_base64(memoryview(stdout)[start:end], strict_mode=True)
        files, refusals = _read_archive(raw, max_files, max_bytes)
    except (binascii.Error, ValueError, tarfile.TarError, OSError, EOFError):
        return user, {}, missing
    return user, files, refusals
