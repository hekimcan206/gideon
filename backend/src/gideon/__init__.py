"""GIDEON: a voice desktop assistant backend.

The audio pipeline (capture -> wake word -> end-of-utterance -> STT -> skills -> TTS -> playback) talks to the
Electron UI over a versioned, typed WebSocket protocol (:mod:`gideon.protocol`).
"""

__version__ = "1.0.0"
