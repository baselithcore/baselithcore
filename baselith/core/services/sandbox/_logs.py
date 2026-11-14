"""Bounded reads of a sandbox container's stdout/stderr.

``container.logs()`` returns the whole stream as one ``bytes`` object. The code
inside the container is untrusted and its output is the one thing it fully
controls: a loop that prints until the timeout hands the API process a buffer
of whatever size the attacker chose. Reading the log as a stream and stopping
at a budget keeps the worst case at the budget.
"""

from __future__ import annotations

from typing import Any

#: Bytes of one stream (stdout or stderr) returned to the caller at most. The
#: observation is truncated again, much lower, before it reaches a model; this
#: bound exists to protect the process that reads it.
MAX_LOG_BYTES = 1024 * 1024
TRUNCATION_NOTICE = "\n[... output truncated ...]"


def read_capped_logs(
    container: Any,
    *,
    stdout: bool,
    stderr: bool,
    limit: int = MAX_LOG_BYTES,
) -> str:
    """Read one log stream of ``container``, keeping at most ``limit`` bytes.

    Args:
        container: A docker-py ``Container``.
        stdout: Include the stdout stream.
        stderr: Include the stderr stream.
        limit: Byte budget; the read stops as soon as it is exceeded.

    Returns:
        The decoded, stripped output, with :data:`TRUNCATION_NOTICE` appended
        when the stream held more than ``limit`` bytes. A multi-byte character
        cut by the budget decodes as U+FFFD rather than raising.
    """
    chunks: list[bytes] = []
    held = 0
    truncated = False
    stream = container.logs(stdout=stdout, stderr=stderr, stream=True)
    if isinstance(stream, (bytes, bytearray)):
        # A client that ignored ``stream`` and returned the buffer whole.
        stream = iter((bytes(stream),))
    try:
        for chunk in stream:
            room = limit - held
            if len(chunk) > room:
                chunks.append(chunk[:room])
                truncated = True
                break
            chunks.append(chunk)
            held += len(chunk)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    text = b"".join(chunks).decode("utf-8", errors="replace").strip()
    return text + TRUNCATION_NOTICE if truncated else text


__all__ = ["MAX_LOG_BYTES", "TRUNCATION_NOTICE", "read_capped_logs"]
