"""Engine interfaces for every pipeline stage.

The pipeline depends only on these protocols, never on openwakeword / faster-whisper / edge-tts directly. Unit
tests drive it with deterministic fakes; the real engines live in :mod:`gideon.engines` and are imported lazily,
so the core package installs and runs without any ML dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000
# 80 ms: openwakeword's native hop. One frame size for the whole pipeline keeps timestamps comparable.
FRAME_SAMPLES = 1_280
FRAME_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE

Pcm = npt.NDArray[np.int16]


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """80 ms of 16 kHz mono int16 audio plus the monotonic time it became available to the pipeline."""

    pcm: Pcm
    captured_at_ns: int
    index: int


@runtime_checkable
class AudioSource(Protocol):
    name: str

    def frames(self) -> AsyncIterator[AudioFrame]: ...


@runtime_checkable
class WakeWordDetector(Protocol):
    name: str

    def process(self, pcm: Pcm) -> float:
        """Score in [0, 1] for the most recent audio; called once per frame, in order."""
        ...

    def reset(self) -> None: ...


@runtime_checkable
class VoiceActivityDetector(Protocol):
    name: str

    def is_speech(self, pcm: Pcm) -> bool: ...


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    language: str
    # exp(mean token log-probability) of the chosen decoding, for logs and traces; 1.0 for typed text.
    confidence: float = 1.0


@runtime_checkable
class SpeechToText(Protocol):
    name: str

    async def transcribe(self, pcm: Pcm) -> Transcription: ...


@runtime_checkable
class TextToSpeech(Protocol):
    name: str

    def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        """Encoded audio chunks as they arrive; the first chunk's arrival is the TTS time-to-first-byte."""
        ...


@runtime_checkable
class AudioSink(Protocol):
    name: str

    async def play(self, chunks: AsyncIterator[bytes], on_started: Callable[[], None]) -> None:
        """Play a streamed answer; call ``on_started`` once when the first audio is handed to the output.

        Must stop promptly on task cancellation (barge-in).
        """
        ...
