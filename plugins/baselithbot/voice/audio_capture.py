"""Audio capture backends for wake-word detection.

Two implementations are wired:

- ``SoundDeviceAudioBackend`` — captures raw PCM via the ``sounddevice``
  library. Suitable for offline wake-word engines (Vosk, Picovoice, custom
  energy-threshold detectors).
- ``EnergyThresholdWake`` — convenience wrapper that polls the audio
  backend and returns the chosen wake-phrase whenever the rolling RMS
  energy crosses a configurable threshold. Useful as a smoke test or as a
  fallback when no real wake-word engine is installed.
"""

from __future__ import annotations

import asyncio
import math
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


class AudioBackendError(RuntimeError):
    """Raised when the underlying audio library is missing or fails."""


class SoundDeviceAudioBackend:
    """Capture raw PCM frames using ``sounddevice``."""

    def __init__(
        self,
        sample_rate: int = 16000,
        block_size: int = 1024,
        channels: int = 1,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.channels = channels
        self._stream: Any | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AudioBackendError(
                "sounddevice not installed; pip install sounddevice numpy"
            ) from exc
        return sd

    async def start(self) -> None:
        sd = self._load()
        self._loop = asyncio.get_running_loop()

        def _callback(indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
            try:
                payload = bytes(indata)
                if self._loop is not None:
                    self._loop.call_soon_threadsafe(self._enqueue, payload)
            except Exception as exc:
                logger.warning("baselithbot_audio_callback_error", error=str(exc))

        with self._lock:
            stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                channels=self.channels,
                dtype="int16",
                callback=_callback,
            )
            stream.start()
            self._stream = stream

    def _enqueue(self, payload: bytes) -> None:
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            self._queue.get_nowait()
            self._queue.put_nowait(payload)

    async def read_frame(self, timeout: float = 1.0) -> bytes | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except TimeoutError:
            return None

    async def stop(self) -> None:
        with self._lock:
            if self._stream is None:
                return
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None


def _rms_energy(frame: bytes) -> float:
    if not frame:
        return 0.0
    samples_count = len(frame) // 2
    if samples_count == 0:
        return 0.0
    samples: Iterable[int] = (
        int.from_bytes(frame[i : i + 2], "little", signed=True)
        for i in range(0, samples_count * 2, 2)
    )
    total = 0
    n = 0
    for s in samples:
        total += s * s
        n += 1
    return math.sqrt(total / n) if n else 0.0


class EnergyThresholdWake:
    """Trigger a wake event when rolling RMS exceeds a threshold."""

    def __init__(
        self,
        backend: SoundDeviceAudioBackend,
        threshold_rms: float = 1500.0,
        window_frames: int = 5,
        wake_phrase: str = "wake",
    ) -> None:
        self._backend = backend
        self._threshold = threshold_rms
        self._window_frames = window_frames
        self._wake_phrase = wake_phrase
        self._window: deque[float] = deque(maxlen=window_frames)

    def make_async_callable(self) -> Callable[[], Awaitable[str]]:
        async def _wait() -> str:
            while True:
                frame = await self._backend.read_frame()
                if frame is None:
                    continue
                self._window.append(_rms_energy(frame))
                if (
                    len(self._window) >= self._window_frames
                    and (sum(self._window) / len(self._window)) >= self._threshold
                ):
                    self._window.clear()
                    return self._wake_phrase

        return _wait


__all__ = [
    "SoundDeviceAudioBackend",
    "EnergyThresholdWake",
    "AudioBackendError",
]
