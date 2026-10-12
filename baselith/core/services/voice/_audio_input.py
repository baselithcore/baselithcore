"""Load an STT request's audio once, off the event loop, under the upload cap.

Every provider path used to call ``STTRequest.get_audio_bytes()`` itself, so a
request built from ``audio_file`` was read synchronously on the event loop
(stalling every in-flight request for the duration of the read), read a second
time for the usage report, and — on the OpenAI SDK and Google paths — never
measured against ``VOICE_MAX_AUDIO_BYTES`` at all. ``load_audio`` is the single
entry point: it applies the cap to every provider, refuses an oversized file
from its ``stat`` size before reading a byte, reads in a worker thread, and
caches the bytes on the request so later ``get_audio_bytes()`` calls are free.
"""

from __future__ import annotations

import asyncio
import os

from baselith.core.services.voice.errors import VoiceInputError, VoiceLimitError
from baselith.core.services.voice.models import STTRequest

_TOO_LARGE = "audio exceeds the upload cap"


def _read_file_capped(path: str, max_bytes: int) -> bytes:
    """Read ``path`` in full, refusing it before the read when it is over the cap.

    The ``stat`` check is the cheap rejection; the ``read(max_bytes + 1)`` after
    it is what holds when the file grows between the two calls.
    """
    try:
        if os.stat(path).st_size > max_bytes:
            raise VoiceLimitError(_TOO_LARGE)
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        # The path is operator/caller input; the error text would echo it.
        raise VoiceInputError("the audio file could not be read") from None
    if len(data) > max_bytes:
        raise VoiceLimitError(_TOO_LARGE)
    return data


async def load_audio(request: STTRequest, max_bytes: int) -> bytes:
    """Return the request's audio, capped at ``max_bytes`` and cached on it.

    Args:
        request: The STT request; ``audio_data`` wins over ``audio_file``.
        max_bytes: ``VOICE_MAX_AUDIO_BYTES``.

    Raises:
        VoiceLimitError: The audio is larger than ``max_bytes``.
        VoiceInputError: Neither bytes nor a readable file was supplied.
    """
    if request.audio_data:
        if len(request.audio_data) > max_bytes:
            raise VoiceLimitError(_TOO_LARGE)
        return request.audio_data
    if request.audio_file:
        data = await asyncio.to_thread(_read_file_capped, request.audio_file, max_bytes)
        request.audio_data = data
        return data
    raise VoiceInputError("no audio data was supplied")


__all__ = ["load_audio"]
