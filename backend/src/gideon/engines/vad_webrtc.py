"""WebRTC VAD adapter: a GMM-based detector that costs microseconds per frame, which suits per-frame endpointing.

webrtcvad accepts 10/20/30 ms frames, so each 80 ms pipeline frame is split into four 20 ms sub-frames and called
speech if at least ``min_voiced`` of them are voiced.
"""

from __future__ import annotations

from typing import Any

from gideon.audio.interfaces import SAMPLE_RATE, Pcm

_SUB = SAMPLE_RATE * 20 // 1000  # 320 samples


class WebRtcVad:
    def __init__(self, aggressiveness: int = 2, min_voiced: int = 2) -> None:
        import webrtcvad

        if not 0 <= aggressiveness <= 3:
            raise ValueError("aggressiveness must be 0..3")
        self._vad: Any = webrtcvad.Vad(aggressiveness)
        self.min_voiced = min_voiced
        self.name = f"webrtcvad:{aggressiveness}"

    def is_speech(self, pcm: Pcm) -> bool:
        voiced = 0
        for start in range(0, len(pcm) - _SUB + 1, _SUB):
            if self._vad.is_speech(pcm[start : start + _SUB].tobytes(), SAMPLE_RATE):
                voiced += 1
        return voiced >= self.min_voiced
