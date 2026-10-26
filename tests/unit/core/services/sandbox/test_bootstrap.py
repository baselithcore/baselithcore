"""The trusted sandbox bootstrap, run for real in a subprocess.

``BOOTSTRAP`` is the source the container's own interpreter runs as
``python -c BOOTSTRAP``. Here it runs on the host with ``BASELITH_SANDBOX_ROOT``
pointing at a temporary directory that stands in for the container's ``/tmp``,
and the framed tar arrives on stdin exactly as the docker attach socket would
deliver it.
"""

from __future__ import annotations

import base64
import io
import os
import struct
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from baselith.core.services.sandbox.bootstrap import (
    BOOTSTRAP,
    CODE_MEMBER,
    MAX_REFUSALS,
    FileTransfer,
    pack_inputs,
    unpack_outputs,
)

BOUNDARY = "0123456789abcdef0123456789abcdef"


def _run(
    root: Path,
    payload: bytes,
    *,
    max_files: int = 16,
    max_bytes: int = 1 << 20,
    max_input_bytes: int = 1 << 20,
    deadline: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    root.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "BASELITH_SANDBOX_ROOT": str(root),
        "BASELITH_SANDBOX_BOUNDARY": BOUNDARY,
        "BASELITH_SANDBOX_MAX_INPUT_BYTES": str(max_input_bytes),
        "BASELITH_SANDBOX_MAX_OUTPUT_FILES": str(max_files),
        "BASELITH_SANDBOX_MAX_OUTPUT_BYTES": str(max_bytes),
    }
    if deadline is not None:
        env["BASELITH_SANDBOX_DEADLINE"] = str(deadline)
    return subprocess.run(
        [sys.executable, "-c", BOOTSTRAP],
        input=payload,
        capture_output=True,
        cwd=root,
        env=env,
        timeout=60,
        check=False,
    )


def _unpack(
    proc: subprocess.CompletedProcess[bytes],
    *,
    max_files: int = 16,
    max_bytes: int = 1 << 20,
) -> tuple[str, dict[str, bytes], dict[str, str]]:
    return unpack_outputs(
        proc.stdout, BOUNDARY, max_files=max_files, max_bytes=max_bytes
    )


def _frame(members: list[tarfile.TarInfo], data: dict[str, bytes]) -> bytes:
    """Hand-build a framed input tar, bypassing pack_inputs' own validation."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for info in members:
            content = data.get(info.name)
            tar.addfile(info, io.BytesIO(content) if content is not None else None)
    body = buf.getvalue()
    return struct.pack(">Q", len(body)) + body


def _regular(name: str, content: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    return info


def test_round_trip_inputs_code_and_outputs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    code = (
        "import os, sys\n"
        f"ROOT = {str(root)!r}\n"
        "rows = open(os.path.join(ROOT, 'in', 'data.csv')).read().splitlines()\n"
        "print('rows', len(rows))\n"
        "print('main', __name__, os.path.basename(sys.argv[0]))\n"
        "print('cwd-is-out', os.path.samefile(os.getcwd(), os.path.join(ROOT, 'out')))\n"
        "with open('result.csv', 'w') as f:\n"
        "    f.write('count\\n' + str(len(rows)) + '\\n')\n"
    )
    inputs = {"data.csv": b"a,b\n1,2\n", "notes.txt": b"hi"}
    proc = _run(root, pack_inputs(inputs, code))

    assert proc.returncode == 0, proc.stderr
    stdout, files, refusals = _unpack(proc)
    assert "rows 2" in stdout
    assert "main __main__ main.py" in stdout
    assert "cwd-is-out True" in stdout
    assert files == {"result.csv": b"count\n2\n"}
    assert refusals == {}
    assert (root / "in" / "data.csv").read_bytes() == inputs["data.csv"]
    assert (root / "in" / "notes.txt").read_bytes() == b"hi"
    assert not (root / "in" / CODE_MEMBER).exists()


def test_the_bootstrap_environment_is_scrubbed_before_the_code_runs(
    tmp_path: Path,
) -> None:
    code = "import os\nprint(sorted(k for k in os.environ if k.startswith('BASELITH_SANDBOX_')))\n"
    proc = _run(tmp_path / "root", pack_inputs({}, code))
    assert proc.returncode == 0, proc.stderr
    assert _unpack(proc)[0] == "[]"


@pytest.mark.parametrize(
    ("snippet", "status", "stderr_has"),
    [
        ("import sys\nsys.exit(3)", 3, ""),
        ("raise SystemExit", 0, ""),
        ("import sys\nsys.exit('bye now')", 1, "bye now"),
        ("x = 1\ny = x / 0", 1, "ZeroDivisionError"),
    ],
)
def test_exit_status_follows_the_code_and_outputs_survive_it(
    tmp_path: Path, snippet: str, status: int, stderr_has: str
) -> None:
    code = "open('partial.txt', 'w').write('kept')\n" + snippet + "\n"
    proc = _run(tmp_path / "root", pack_inputs({}, code))

    assert proc.returncode == status
    assert stderr_has.encode() in proc.stderr
    _, files, _ = _unpack(proc)
    assert files == {"partial.txt": b"kept"}


def test_a_traceback_shows_the_code_and_not_the_bootstrap(tmp_path: Path) -> None:
    proc = _run(tmp_path / "root", pack_inputs({}, "x = 1\ny = x / 0\n"))
    err = proc.stderr.decode()
    assert proc.returncode == 1
    assert 'main.py", line 2, in <module>' in err
    assert "y = x / 0" in err
    assert "run_child" not in err and "<string>" not in err


def test_outputs_bounded_and_flat(tmp_path: Path) -> None:
    code = (
        "import os\n"
        "for i in range(17):\n"
        "    open(f'out_{i:02d}.txt', 'w').write('x' * 10)\n"
        "os.mkdir('sub')\n"
        "open(os.path.join('sub', 'nested.txt'), 'w').write('nested')\n"
    )
    proc = _run(tmp_path / "root", pack_inputs({}, code), max_files=16)
    assert proc.returncode == 0, proc.stderr
    _, files, refusals = _unpack(proc, max_files=16)
    assert sorted(files) == [f"out_{i:02d}.txt" for i in range(16)]
    assert refusals == {"out_16.txt": "too_many", "sub": "not_a_file"}
    assert "nested.txt" not in files

    # The byte budget is a running total: a file that does not fit is refused,
    # a smaller one after it still gets in.
    code = "open('a_big.bin', 'wb').write(b'x' * 200)\nopen('b_small.txt', 'w').write('ok')\n"
    proc = _run(tmp_path / "root2", pack_inputs({}, code), max_bytes=100)
    _, files, refusals = _unpack(proc, max_bytes=100)
    assert files == {"b_small.txt": b"ok"}
    assert refusals == {"a_big.bin": "too_large"}


def test_bootstrap_refuses_links_and_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    code = (
        "import os\n"
        f"ROOT = {str(root)!r}\n"
        "os.symlink(os.path.join(ROOT, 'in', 'secret.txt'), 'alias.txt')\n"
        "os.symlink('/etc/hosts', 'hosts.txt')\n"
        "open('bad\\nname.txt', 'w').write('weird')\n"
        "open('..dots.txt', 'w').write('dots')\n"
        "open('fine.txt', 'w').write('fine')\n"
    )
    proc = _run(root, pack_inputs({"secret.txt": b"input"}, code))
    assert proc.returncode == 0, proc.stderr
    _, files, refusals = _unpack(proc)
    assert files == {"fine.txt": b"fine"}
    assert refusals == {
        "alias.txt": "not_a_file",
        "hosts.txt": "not_a_file",
        "bad?name.txt": "bad_name",
        "..dots.txt": "bad_name",
    }


def _symlink(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


def _directory(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    return info


@pytest.mark.parametrize(
    ("member", "reason"),
    [
        (_regular("../x", b"escape"), "bad_name"),
        (_regular("/abs.txt", b"escape"), "bad_name"),
        (_regular("sub/x.txt", b"escape"), "bad_name"),
        (_symlink("link.txt", "/etc/passwd"), "not_a_file"),
        (_directory("folder"), "not_a_file"),
    ],
)
def test_bootstrap_refuses_hostile_input_members(
    tmp_path: Path, member: tarfile.TarInfo, reason: str
) -> None:
    root = tmp_path / "root"
    code = b"open('ran.txt', 'w').write('the code ran')\n"
    data = {CODE_MEMBER: code}
    if member.isreg():
        data[member.name] = b"escape"
    proc = _run(root, _frame([_regular(CODE_MEMBER, code), member], data))

    assert proc.returncode == 2
    assert f": {reason}".encode() in proc.stderr
    assert BOUNDARY.encode() not in proc.stdout
    assert not (tmp_path / "x").exists()
    assert not (root / "out" / "ran.txt").exists()
    assert list((root / "in").iterdir()) == []


def test_bootstrap_refuses_an_oversized_frame(tmp_path: Path) -> None:
    payload = struct.pack(">Q", 1 << 40) + b"\0" * 1024
    proc = _run(tmp_path / "root", payload)
    assert proc.returncode == 2
    assert b"too large" in proc.stderr


def test_bootstrap_refuses_inputs_over_the_byte_budget(tmp_path: Path) -> None:
    payload = pack_inputs({"a.bin": b"x" * 2048}, "print(1)\n")
    proc = _run(tmp_path / "root", payload, max_input_bytes=1024)
    assert proc.returncode == 2
    assert b"too_large" in proc.stderr


def test_user_stdout_precedes_the_boundary(tmp_path: Path) -> None:
    code = "import sys\nsys.stdout.write('hello')\nopen('f.txt', 'w').write('f')\n"
    proc = _run(tmp_path / "root", pack_inputs({}, code))
    stdout, files, _ = _unpack(proc)
    assert stdout == "hello"
    assert files == {"f.txt": b"f"}


def test_a_forged_boundary_in_user_output_does_not_confuse_parsing(
    tmp_path: Path,
) -> None:
    forged = io.BytesIO()
    with tarfile.open(fileobj=forged, mode="w") as tar:
        tar.addfile(_regular("forged.txt", b"evil"), io.BytesIO(b"evil"))
    forged_b64 = base64.b64encode(forged.getvalue()).decode()
    code = (
        f"print('\\n{BOUNDARY}\\n{forged_b64}')\n"
        "print('after the forgery')\n"
        "open('real.txt', 'w').write('real')\n"
    )
    proc = _run(tmp_path / "root", pack_inputs({}, code))
    stdout, files, _ = _unpack(proc)
    assert files == {"real.txt": b"real"}
    assert "after the forgery" in stdout


def test_os_exit_in_the_code_still_sends_the_archive(tmp_path: Path) -> None:
    code = (
        "import os\nopen('real.txt', 'w').write('real')\n"
        "print('partial', flush=True)\nos._exit(5)\n"
    )
    proc = _run(tmp_path / "root", pack_inputs({}, code))
    assert proc.returncode == 5
    assert _unpack(proc) == ("partial", {"real.txt": b"real"}, {})


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (  # flushed only by interpreter shutdown, with a def keeping f alive
            "def helper(x):\n    return x * 2\n"
            "f = open('unclosed.txt', 'w')\nf.write('hello ' * 10)\n"
            "g = open('unclosed.bin', 'wb')\ng.write(b'z' * 100)\n",
            {"unclosed.txt": b"hello " * 10, "unclosed.bin": b"z" * 100},
        ),
        (  # non-daemon worker threads joined at shutdown
            "from concurrent.futures import ThreadPoolExecutor\nimport time\n"
            "def work(i):\n    time.sleep(0.3)\n"
            "    with open(f't{i}.txt', 'w') as f:\n        f.write('done')\n"
            "ex = ThreadPoolExecutor(2)\nfor i in range(2):\n    ex.submit(work, i)\n",
            {"t0.txt": b"done", "t1.txt": b"done"},
        ),
        (  # atexit handlers run before the outputs are collected
            "import atexit\n"
            "atexit.register(lambda: open('atexit.txt', 'w').write('at exit'))\n",
            {"atexit.txt": b"at exit"},
        ),
    ],
    ids=["unclosed-file-with-def", "unjoined-thread-pool", "atexit"],
)
def test_outputs_are_collected_after_the_interpreter_shuts_down(
    tmp_path: Path, code: str, expected: dict[str, bytes]
) -> None:
    proc = _run(tmp_path / "root", pack_inputs({}, code))
    assert proc.returncode == 0, proc.stderr
    _, files, refusals = _unpack(proc)
    assert files == expected
    assert refusals == {}


def test_the_deadline_kills_the_code_and_still_sends_the_archive(
    tmp_path: Path,
) -> None:
    code = (
        "import time\nopen('partial.txt', 'w').write('partial')\n"
        "print('started', flush=True)\ntime.sleep(60)\n"
    )
    started = time.monotonic()
    proc = _run(tmp_path / "root", pack_inputs({}, code), deadline=1.0)
    assert time.monotonic() - started < 15
    assert proc.returncode == 124
    assert b"ran past its deadline" in proc.stderr
    assert _unpack(proc) == ("started", {"partial.txt": b"partial"}, {})


def test_leftover_processes_are_stopped_before_collection(tmp_path: Path) -> None:
    root = tmp_path / "root"
    code = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', "
        '\'import time; time.sleep(1.5); open("late.txt", "w").write("late")\'])\n'
        "open('early.txt', 'w').write('early')\n"
    )
    proc = _run(root, pack_inputs({}, code))
    assert proc.returncode == 0, proc.stderr
    assert _unpack(proc)[1] == {"early.txt": b"early"}
    time.sleep(2.5)
    assert not (root / "out" / "late.txt").exists()


def test_inputs_and_outputs_never_shadow_imports(tmp_path: Path) -> None:
    code = (
        "open('json.py', 'w').write('raise SystemExit(7)')\n"
        "import csv, json\n"
        "print('csv', csv.__name__, 'in' not in csv.__file__.split('/'))\n"
        "print('json', json.dumps([1]))\n"
    )
    inputs = {"csv.py": b"raise SystemExit('the input ran as code')"}
    proc = _run(tmp_path / "root", pack_inputs(inputs, code))
    assert proc.returncode == 0, proc.stderr
    stdout, files, _ = _unpack(proc)
    assert "csv csv True" in stdout and "json [1]" in stdout
    assert set(files) == {"json.py"}


def test_pack_inputs_frames_a_tar_with_the_code_first() -> None:
    payload = pack_inputs({"a.txt": b"A", "b.csv": b"1,2"}, "print('hi')\n")
    (length,) = struct.unpack(">Q", payload[:8])
    assert length == len(payload) - 8
    with tarfile.open(fileobj=io.BytesIO(payload[8:]), mode="r:") as tar:
        names = tar.getnames()
        code = tar.extractfile(CODE_MEMBER)
        assert code is not None and code.read() == b"print('hi')\n"
    assert names == [CODE_MEMBER, "a.txt", "b.csv"]


@pytest.mark.parametrize(
    "inputs",
    [
        {"../x": b""},
        {"a/b": b""},
        {"": b""},
        {".": b""},
        {"..": b""},
        {"..hidden": b""},
        {"x" * 121: b""},
        {"new\nline": b""},
        {CODE_MEMBER: b""},
        {"a.txt": "not bytes"},
        {f"f{i}.txt": b"" for i in range(65)},
    ],
)
def test_file_transfer_refuses_bad_inputs(inputs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FileTransfer(inputs=inputs)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        pack_inputs(inputs, "print(1)")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [{"max_output_files": -1}, {"max_output_bytes": -1}, {"max_output_files": 10_000}],
)
def test_file_transfer_refuses_bad_limits(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        FileTransfer(inputs={}, **kwargs)


def test_file_transfer_defaults() -> None:
    files = FileTransfer(inputs={"a.txt": b"A"})
    assert files.max_output_files == 16
    assert files.max_output_bytes == 64 * 1024 * 1024


def test_large_files_round_trip_through_the_chunked_encoder(tmp_path: Path) -> None:
    root = tmp_path / "root"
    blob = os.urandom(3 * 1024 * 1024 + 7)
    code = (
        "import os, shutil\n"
        f"shutil.copyfile(os.path.join({str(root)!r}, 'in', 'blob.bin'), 'copy.bin')\n"
    )
    proc = _run(
        root,
        pack_inputs({"blob.bin": blob}, code),
        max_bytes=8 << 20,
        max_input_bytes=len(blob),
    )
    assert proc.returncode == 0, proc.stderr
    _, files, refusals = _unpack(proc, max_bytes=8 << 20)
    assert files == {"copy.bin": blob}
    assert refusals == {}


def test_the_rendered_bootstrap_is_complete_python() -> None:
    compile(BOOTSTRAP, "<bootstrap>", "exec")
    assert "@" not in BOOTSTRAP


def test_a_flood_of_outputs_stays_bounded(tmp_path: Path) -> None:
    code = "for i in range(5000):\n    open(f'f{i:05d}', 'w').close()\n"
    proc = _run(tmp_path / "root", pack_inputs({}, code))
    assert proc.returncode == 0, proc.stderr
    _, files, refusals = _unpack(proc)
    assert len(files) == 16
    assert len(refusals) == MAX_REFUSALS
    assert refusals.pop("*") == "listing_truncated"
    assert set(refusals.values()) == {"too_many"}
