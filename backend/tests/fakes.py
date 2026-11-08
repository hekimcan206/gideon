"""Deterministic fakes for every engine interface, and a tiny script language for synthetic audio.

``script("SSW SS VVVV SSSSSSS")`` builds one 80 ms frame per letter: S = silence, V = voiced (a 300 Hz tone the
energy VAD hears), W = a loud "wake" frame the fake wake-word detector fires on. Spaces are ignored.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence

import numpy as np

from gideon.audio.interfaces import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame, Pcm, Transcription
from gideon.latency import Clock

_T = np.arange(FRAME_SAMPLES) / SAMPLE_RATE
SILENCE = np.zeros(FRAME_SAMPLES, dtype=np.int16)
VOICED = (np.sin(2 * np.pi * 300 * _T) * 3000).astype(np.int16)
WAKE = (np.sin(2 * np.pi * 300 * _T) * 20000).astype(np.int16)
_FRAMES = {"S": SILENCE, "V": VOICED, "W": WAKE}


def script(spec: str) -> list[Pcm]:
    return [_FRAMES[c] for c in spec if not c.isspace()]


class ScriptSource:
    """Releases scripted frames as fast as the loop allows, yielding to other tasks after every frame.

    ``pause_after`` maps a frame index to extra event-loop turns, which lets a test give the response task time to
    reach a given stage (e.g. *speaking*) before the next frame arrives.
    """

    name = "script"

    def __init__(
        self,
        frames: Sequence[Pcm],
        *,
        clock: Clock,
        pause_after: dict[int, float] | None = None,
    ) -> None:
        self._frames = list(frames)
        self.clock = clock
        self.pause_after = pause_after or {}
        self.released: list[AudioFrame] = []

    async def frames(self) -> AsyncIterator[AudioFrame]:
        for index, pcm in enumerate(self._frames):
            await asyncio.sleep(0)
            frame = AudioFrame(pcm=pcm, captured_at_ns=self.clock(), index=index)
            self.released.append(frame)
            yield frame
            if index in self.pause_after:
                await asyncio.sleep(self.pause_after[index])


class FakeWake:
    name = "fake-wake"

    def __init__(self) -> None:
        self.resets = 0
        self.frames_seen = 0

    def process(self, pcm: Pcm) -> float:
        self.frames_seen += 1
        return 0.97 if int(np.abs(pcm.astype(np.int32)).max()) >= 15_000 else 0.01

    def reset(self) -> None:
        self.resets += 1


class FakeStt:
    name = "fake-stt"

    def __init__(
        self,
        text: str | Callable[[Pcm], str] = "what time is it",
        language: str = "en",
        delay_s: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.language = language
        self.delay_s = delay_s
        self.error = error
        self.calls: list[int] = []

    async def transcribe(self, pcm: Pcm) -> Transcription:
        self.calls.append(len(pcm))
        await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        text = self.text(pcm) if callable(self.text) else self.text
        return Transcription(text=text, language=self.language, confidence=0.9)


class FakeTts:
    name = "fake-tts"

    def __init__(self, chunks: int = 3, first_delay_s: float = 0.0, chunk_delay_s: float = 0.0) -> None:
        self.chunks = chunks
        self.first_delay_s = first_delay_s
        self.chunk_delay_s = chunk_delay_s
        self.spoken: list[tuple[str, str]] = []
        self.chunks_sent = 0

    async def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        self.spoken.append((text, language))
        await asyncio.sleep(self.first_delay_s)
        for _ in range(self.chunks):
            self.chunks_sent += 1
            yield b"\x00" * 600
            await asyncio.sleep(self.chunk_delay_s)


class FakeClock:
    """Monotonic nanosecond clock that a test advances explicitly."""

    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self.now = start_ns

    def __call__(self) -> int:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += int(ms * 1_000_000)
