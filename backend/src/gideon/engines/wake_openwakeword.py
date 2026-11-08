"""openWakeWord adapter (ONNX runtime)."""

from __future__ import annotations

from typing import Any

from gideon.audio.interfaces import Pcm


class OpenWakeWordDetector:
    """Wraps one openWakeWord model.

    openWakeWord computes a melspectrogram and a shared speech-embedding model per 80 ms frame and keeps a rolling
    window, so it must see *every* frame in order; the pipeline feeds it even while listening and ignores the
    score instead of skipping frames. ``reset()`` clears the prediction buffer after a detection; otherwise the
    score stays above threshold for several frames and re-triggers.
    """

    def __init__(self, model: str = "hey_jarvis", *, download: bool = True) -> None:
        import openwakeword
        from openwakeword.model import Model

        if download:
            openwakeword.utils.download_models([model])
        self._model: Any = Model(wakeword_models=[model], inference_framework="onnx")
        self._key = next(iter(self._model.models.keys()))
        self.name = f"openwakeword:{self._key}"

    def process(self, pcm: Pcm) -> float:
        scores = self._model.predict(pcm)
        return float(scores[self._key])

    def reset(self) -> None:
        self._model.reset()
