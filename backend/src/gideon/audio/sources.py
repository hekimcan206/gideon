"""Audio sources that need no audio hardware: WAV files paced like a microphone."""

from __future__ import annotations

import asyncio
import time
import wave
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import numpy as np

from gideon.audio.interfaces import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame, Pcm
from gideon.latency import Clock


def read_wav(path: Path) -> Pcm:
    """Read a 16 kHz mono 16-bit PCM WAV. Other formats are rejected rather than silently resampled."""
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(
                f"{path}: need 16 kHz mono 16-bit PCM, got {wav.getframerate()} Hz, "
                f"{wav.getnchannels()} ch, {8 * wav.getsampwidth()} bit"
            )
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).copy()


def write_wav(path: Path, pcm: Pcm) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.astype(np.int16).tobytes())


def silence(seconds: float) -> Pcm:
    return np.zeros(round(seconds * SAMPLE_RATE), dtype=np.int16)


def to_frames(pcm: Pcm) -> list[Pcm]:
    """Split into 80 ms frames, zero-padding the last one."""
    pad = (-len(pcm)) % FRAME_SAMPLES
    if pad:
        pcm = np.concatenate([pcm, np.zeros(pad, dtype=np.int16)])
    return [pcm[i : i + FRAME_SAMPLES] for i in range(0, len(pcm), FRAME_SAMPLES)]


class WavSource:
    """Plays WAV files into the pipeline as if a microphone were capturing them.

    In real-time mode frame *n* is released no earlier than ``start + (n + 1) * 80 ms`` and stamped with the time it
    was actually released, exactly like a capture callback delivering a full buffer. That keeps every latency
    number measured with this source comparable to a live run: the pipeline cannot "see" audio before it would
    have been spoken. Silence is inserted between files and after the last one so trailing interactions finish.
    """

    name = "wav"

    def __init__(
        self,
        paths: Sequence[Path],
        *,
        realtime: bool = True,
        loop: bool = False,
        gap_s: float = 1.5,
        tail_s: float = 6.0,
        clock: Clock = time.monotonic_ns,
    ) -> None:
        if not paths:
            raise ValueError("WavSource needs at least one file")
        self.paths = list(paths)
        self.realtime = realtime
        self.loop = loop
        self.gap_s = gap_s
        self.tail_s = tail_s
        self.clock = clock

    async def frames(self) -> AsyncIterator[AudioFrame]:
        frame_ns = FRAME_SAMPLES * 1_000_000_000 // SAMPLE_RATE
        start = self.clock()
        index = 0
        while True:
            for path in self.paths:
                for pcm in to_frames(np.concatenate([read_wav(path), silence(self.gap_s)])):
                    yield await self._release(pcm, index, start, frame_ns)
                    index += 1
            if not self.loop:
                break
        for pcm in to_frames(silence(self.tail_s)):
            yield await self._release(pcm, index, start, frame_ns)
            index += 1

    async def _release(self, pcm: Pcm, index: int, start: int, frame_ns: int) -> AudioFrame:
        if self.realtime:
            due = start + (index + 1) * frame_ns
            delay = (due - self.clock()) / 1e9
            if delay > 0:
                await asyncio.sleep(delay)
        else:
            await asyncio.sleep(0)  # let the response task run between frames
        return AudioFrame(pcm=pcm, captured_at_ns=self.clock(), index=index)
