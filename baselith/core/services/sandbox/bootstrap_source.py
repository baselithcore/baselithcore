"""Source template of the sandbox bootstrap, as the container's python runs it.

:mod:`~baselith.core.services.sandbox.bootstrap` fills the ``@TOKEN@``
placeholders with its constants and exposes the result as ``BOOTSTRAP``; read
that module for the protocol and the trust model. The template is plain
stdlib Python 3.12 with no imports from this package: it runs inside the
sandbox image, not in the host process.

``BASELITH_SANDBOX_ROOT`` (default ``/tmp``) exists for the tests, which run
the bootstrap on the host; the parent never passes it, and strips every
``BASELITH_SANDBOX_*`` key a caller supplies.
"""

SOURCE_TEMPLATE = r"""
import base64, io, json, os, re, signal, stat, struct, subprocess, sys, tarfile, time

NAME = re.compile(r"@NAME@")
BAD = re.compile(r"[^A-Za-z0-9._ -]")


def flat(name):
    return bool(NAME.fullmatch(name)) and name != "." and not name.startswith("..")


def show(name):
    shown = BAD.sub("?", name)
    return shown if len(shown) <= 120 else shown[:119] + "?"


def fail(message):
    try:
        sys.stderr.write("sandbox bootstrap: " + message + "\n")
        sys.stderr.flush()
    finally:
        os._exit(2)


def number(key, default):
    try:
        return max(0, int(os.environ.get(key, default)))
    except ValueError:
        return default


def seconds(key):
    try:
        value = float(os.environ.get(key, ""))
    except ValueError:
        return None
    return value if value > 0 else None


def put(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


class Frame(io.RawIOBase):
    def __init__(self, source, size):
        self.source, self.left = source, size

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.left <= 0:
            return 0
        data = self.source.read(min(len(buffer), self.left))
        if not data:
            fail("the input frame ended early")
        buffer[: len(data)] = data
        self.left -= len(data)
        return len(data)


class Encoder:
    def __init__(self, fd):
        self.fd, self.pending = fd, bytearray()

    def write(self, data):
        self.pending += data
        if len(self.pending) >= 49152:
            cut = len(self.pending) // 3 * 3
            put(self.fd, base64.b64encode(self.pending[:cut]))
            del self.pending[:cut]
        return len(data)

    def finish(self, trailer):
        put(self.fd, base64.b64encode(self.pending) + trailer)


def take_inputs(source, inbox, max_bytes):
    head = source.read(8)
    if len(head) != 8:
        fail("no input frame")
    size = struct.unpack(">Q", head)[0]
    if size > max_bytes + @CODE_CAP@ + (@MAX_IN@ + 1) * @OVERHEAD@ + @TRAILER@:
        fail("the input frame is too large")
    code, seen, total = None, set(), 0
    with tarfile.open(fileobj=Frame(source, size), mode="r|") as tar:
        for member in tar:
            name, regular = member.name, member.isreg() and not member.issparse()
            if name == "@CODE@" and code is None:
                if not regular or member.size > @CODE_CAP@:
                    fail("refused the code member")
                code = tar.extractfile(member).read()
                continue
            if not regular:
                fail("refused input " + show(name) + ": not_a_file")
            if not flat(name) or name == "@CODE@" or name in seen:
                fail("refused input " + show(name) + ": bad_name")
            seen.add(name)
            total += member.size
            if len(seen) > @MAX_IN@:
                fail("refused input " + show(name) + ": too_many")
            if total > max_bytes:
                fail("refused input " + show(name) + ": too_large")
            data = tar.extractfile(member)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            with os.fdopen(os.open(os.path.join(inbox, name), flags, 0o600), "wb") as sink:
                while chunk := data.read(1 << 20):
                    sink.write(chunk)
    if code is None:
        fail("no code member")
    return code


def stop_leftovers(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except OSError:
        pass
    if os.getpid() != 1:  # only the container's init may sweep its namespace
        return
    for _ in range(50):
        others = [int(entry) for entry in os.listdir("/proc") if entry.isdigit() and entry != "1"]
        if not others:
            return
        for pid in others:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
        time.sleep(0.02)


def run_child(code, root, outbox, deadline):
    folder = os.path.join(root, "run")
    os.mkdir(folder, 0o700)
    script = os.path.join(folder, "main.py")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(script, flags, 0o600), "wb") as sink:
        sink.write(code)
    child = subprocess.Popen(
        [sys.executable, script], cwd=outbox, stdin=subprocess.DEVNULL, start_new_session=True
    )
    try:
        status = child.wait(None if deadline is None else max(deadline - time.monotonic(), 0.05))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            child.kill()
        child.wait()
        sys.stderr.write("sandbox bootstrap: the code ran past its deadline and was stopped\n")
        sys.stderr.flush()
        status = 124
    stop_leftovers(child)
    return status if status >= 0 else 128 - status


def consider(folder, name, keep, max_files, room):
    info = os.stat(name, dir_fd=folder, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        return "not_a_file"
    if len(keep) >= max_files:
        return "too_many"
    if info.st_size > room:
        return "too_large"
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > room:
        os.close(fd)
        return "not_a_file"
    keep.append((name, fd, info.st_size))
    return None


def plan_outputs(outbox, max_files, max_bytes):
    keep, refused = [], {}
    try:
        folder = os.open(outbox, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        sys.stderr.write("sandbox bootstrap: the output folder is gone\n")
        return keep, refused
    try:
        with os.scandir(folder) as entries:
            names = sorted(entry.name for _, entry in zip(range(@SCAN@), entries))
            if next(entries, None) is not None:
                refused["*"] = "listing_truncated"
        for name in names:
            room = max_bytes - sum(size for _, _, size in keep)
            try:
                reason = consider(folder, name, keep, max_files, room) if flat(name) else "bad_name"
            except OSError:
                reason = "not_a_file"
            if reason and len(refused) < @MAX_REFUSALS@:
                refused.setdefault(show(name), reason)
    finally:
        os.close(folder)
    return keep, refused


def add(tar, name, stream, size):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, 0o644, 0
    tar.addfile(info, stream)


def emit(fd, boundary, keep, refused):
    put(fd, ("\n" + boundary + "\n").encode())
    encoder, trailer = Encoder(fd), b"\n"
    try:
        with tarfile.open(fileobj=encoder, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            manifest = json.dumps(refused, sort_keys=True).encode()
            add(tar, "@META@", io.BytesIO(manifest), len(manifest))
            for name, handle, size in keep:
                with os.fdopen(handle, "rb") as stream:
                    add(tar, name, stream, size)
    except Exception as error:
        trailer = b"!\n"  # not base64: the parent drops the whole archive
        sys.stderr.write("sandbox bootstrap: the outputs were not sent: %s\n" % error)
    encoder.finish(trailer)


def main():
    started = time.monotonic()
    root = os.environ.get("BASELITH_SANDBOX_ROOT", "/tmp")
    boundary = os.environ.get("@ENV_BOUNDARY@", "")
    max_in = number("@ENV_IN_BYTES@", @DEFAULT_IN_BYTES@)
    max_files = number("@ENV_OUT_FILES@", @DEFAULT_OUT_FILES@)
    max_bytes = number("@ENV_OUT_BYTES@", @DEFAULT_OUT_BYTES@)
    budget = seconds("@ENV_DEADLINE@")
    for key in [key for key in os.environ if key.startswith("@ENV_PREFIX@")]:
        del os.environ[key]
    if not re.fullmatch("[0-9a-f]{32}", boundary):
        fail("no boundary")
    inbox, outbox = os.path.join(root, "in"), os.path.join(root, "out")
    try:
        os.mkdir(inbox, 0o700)
        os.mkdir(outbox, 0o700)
        code = take_inputs(sys.stdin.buffer, inbox, max_in)
    except Exception as error:
        fail("the inputs could not be read: %s" % error)
    try:
        status = run_child(code, root, outbox, None if budget is None else started + budget)
    except Exception as error:
        fail("the code could not be started: %s" % error)
    try:
        keep, refused = plan_outputs(outbox, max_files, max_bytes)
        emit(1, boundary, keep, refused)
    except Exception as error:
        try:
            sys.stderr.write("sandbox bootstrap: the outputs were not sent: %s\n" % error)
        except Exception:
            pass
    try:
        sys.stderr.flush()
    finally:
        os._exit(status)


main()
"""
