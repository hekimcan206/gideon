"""Dependency-free energy VAD: the fallback when webrtcvad is not installed, and the VAD used by unit tests."""

from __future__ import annotations

import numpy as np

from gideon.audio.interfaces import Pcm


class EnergyVad:
    name = "energy"

    def __init__(self, threshold_dbfs: float = -45.0) -> None:
        self.threshold_dbfs = threshold_dbfs

    def is_speech(self, pcm: Pcm) -> bool:
        if len(pcm) == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float64)))))
        if rms <= 0.0:
            return False
        return bool(20.0 * np.log10(rms / 32768.0) > self.threshold_dbfs)
