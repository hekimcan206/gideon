"""End-of-utterance detection on top of a frame-level VAD.

A VAD only says "this 80 ms looks like speech". Deciding that the user has *finished* is a policy with a direct
latency cost: every millisecond of silence hangover is added to the response time, while too short a hangover cuts
people off mid-sentence ("set a timer for ... five minutes"). The policy is kept separate from the VAD engine so it
can be unit-tested frame by frame and tuned from configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gideon.audio.interfaces import FRAME_MS, Pcm, VoiceActivityDetector


class EndpointKind(StrEnum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"  # enough trailing silence after speech
    MAX_LENGTH = "max_length"  # user kept talking; cut to protect latency and memory
    NO_SPEECH = "no_speech"  # woken but nobody spoke


@dataclass(frozen=True, slots=True)
class EndpointEvent:
    kind: EndpointKind
    # Capture time of the last voiced frame (None for NO_SPEECH).
    last_voiced_ns: int | None = None


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    silence_ms: int = 560  # 7 frames of hangover
    min_speech_ms: int = 160  # 2 voiced frames before we believe it is speech (clicks, breaths)
    max_utterance_ms: int = 8_000
    no_speech_timeout_ms: int = 4_000


class Endpointer:
    def __init__(self, vad: VoiceActivityDetector, config: EndpointConfig | None = None) -> None:
        self.vad = vad
        self.config = config or EndpointConfig()
        self.reset()

    def reset(self) -> None:
        self._elapsed_ms = 0
        self._voiced_ms = 0
        self._silence_ms = 0
        self._started = False
        self._last_voiced_ns: int | None = None

    @property
    def speech_started(self) -> bool:
        return self._started

    def feed(self, pcm: Pcm, captured_at_ns: int) -> EndpointEvent | None:
        cfg = self.config
        self._elapsed_ms += FRAME_MS
        speech = self.vad.is_speech(pcm)
        if speech:
            self._voiced_ms += FRAME_MS
            self._silence_ms = 0
            self._last_voiced_ns = captured_at_ns
        else:
            self._silence_ms += FRAME_MS
            if not self._started:
                # Isolated voiced frames separated by silence do not accumulate into "speech".
                self._voiced_ms = 0

        if not self._started:
            if self._voiced_ms >= cfg.min_speech_ms:
                self._started = True
                return EndpointEvent(EndpointKind.SPEECH_START, self._last_voiced_ns)
            if self._elapsed_ms >= cfg.no_speech_timeout_ms:
                return EndpointEvent(EndpointKind.NO_SPEECH)
            return None

        if self._silence_ms >= cfg.silence_ms:
            return EndpointEvent(EndpointKind.SPEECH_END, self._last_voiced_ns)
        if self._elapsed_ms >= cfg.max_utterance_ms:
            return EndpointEvent(EndpointKind.MAX_LENGTH, self._last_voiced_ns)
        return None
