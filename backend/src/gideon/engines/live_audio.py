"""Live microphone capture (sounddevice) and speaker playback (miniaudio).

Not exercised in CI (runners have no audio devices); the WAV source and simulated sink cover the same interfaces.
README "Running on real hardware" documents the live run.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import numpy as np

from gideon.audio.interfaces import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame


class MicrophoneSource:
    """16 kHz mono capture in 80 ms blocks; each block is stamped in the audio callback with the monotonic clock."""

    name = "microphone"

    def __init__(self, device: int | str | None = None) -> None:
        self.device = device

    async def frames(self) -> AsyncIterator[AudioFrame]:
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        frames: asyncio.Queue[tuple[np.ndarray[Any, Any], int]] = asyncio.Queue(maxsize=200)

        def callback(indata: Any, _frames: int, _time: Any, _status: Any) -> None:
            stamped = (indata[:, 0].copy(), time.monotonic_ns())
            # Drop audio rather than block the audio thread if the pipeline ever stalls.
            loop.call_soon_threadsafe(lambda: None if frames.full() else frames.put_nowait(stamped))

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=self.device,
            callback=callback,
        )
        index = 0
        with stream:
            while True:
                pcm, captured = await frames.get()
                yield AudioFrame(pcm=pcm.astype(np.int16), captured_at_ns=captured, index=index)
                index += 1


class _ChunkSource:
    """Blocking byte source fed from the event loop; miniaudio's decoder pulls from it on its own thread."""

    def __init__(self) -> None:
        self.queue: queue.Queue[bytes | None] = queue.Queue()
        self._buffer = b""
        self._eof = False

    def read(self, num_bytes: int) -> bytes:
        while len(self._buffer) < num_bytes and not self._eof:
            item = self.queue.get()
            if item is None:
                self._eof = True
            else:
                self._buffer += item
        out, self._buffer = self._buffer[:num_bytes], self._buffer[num_bytes:]
        return out


class SpeakerSink:
    """Streams edge-tts MP3 chunks to the default output device while they are still arriving."""

    name = "speaker"

    def __init__(self, sample_rate: int = 24_000) -> None:
        self.sample_rate = sample_rate

    async def play(self, chunks: AsyncIterator[bytes], on_started: Callable[[], None]) -> None:
        import miniaudio

        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        source = _ChunkSource()
        device: Any = None
        try:
            async for chunk in chunks:
                source.queue.put(chunk)
                if device is None:
                    device = miniaudio.PlaybackDevice(
                        output_format=miniaudio.SampleFormat.SIGNED16,
                        nchannels=1,
                        sample_rate=self.sample_rate,
                    )

                    def build() -> Any:
                        decoded = miniaudio.stream_any(
                            _MiniaudioSource(source),
                            source_format=miniaudio.FileFormat.MP3,
                            output_format=miniaudio.SampleFormat.SIGNED16,
                            nchannels=1,
                            sample_rate=self.sample_rate,
                        )
                        return miniaudio.stream_with_callbacks(
                            decoded, end_callback=lambda: loop.call_soon_threadsafe(done.set)
                        )

                    stream = await asyncio.to_thread(build)
                    next(stream)  # prime the generator as PlaybackDevice expects
                    device.start(stream)
                    on_started()
            source.queue.put(None)
            if device is not None:
                await done.wait()
        finally:
            source.queue.put(None)
            if device is not None:
                with contextlib.suppress(Exception):
                    device.stop()
                    device.close()


class _MiniaudioSource:
    """Adapter giving :class:`_ChunkSource` the ``miniaudio.StreamableSource`` interface lazily."""

    def __new__(cls, inner: _ChunkSource) -> Any:
        import miniaudio

        class Source(miniaudio.StreamableSource):  # type: ignore[misc]
            def read(self, num_bytes: int) -> bytes:
                return inner.read(num_bytes)

        return Source()
