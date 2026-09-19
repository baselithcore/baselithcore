"""Run a command inside a pty of a fixed size.

asciinema records whatever size the terminal it inherits has — 80x24 in a
headless session, and whatever your window happens to be otherwise. The GIFs in
the README are all recorded at the same width so they render consistently, which
this wrapper makes reproducible:

    python media/record.py 100 30 \\
        asciinema rec --overwrite -c "./media/demo.sh" media/demo.cast
"""

from __future__ import annotations

import fcntl
import os
import pty
import struct
import sys
import termios


def run(cols: int, rows: int, argv: list[str]) -> int:
    """Execute ``argv`` in a pty sized ``cols``x``rows``, streaming its output."""
    pid, fd = pty.fork()
    if pid == 0:  # child: becomes the command
        os.execvp(argv[0], argv)

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:  # the child closed the pty
            break
        if not chunk:
            break
        os.write(1, chunk)

    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("usage: record.py <cols> <rows> <command> [args...]")
    sys.exit(run(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]))
