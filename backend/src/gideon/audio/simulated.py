"""Offline TTS stand-in for simulated mode without internet access (``GIDEON_TTS=simulated``).

It produces no intelligible audio: it yields silent bytes at edge-tts' bitrate for the time the sentence would take
to say, after a fixed synthesis delay. It exists so the pipeline, the UI and CI can run fully offline; benchmark
numbers are only ever reported for the real edge-tts engine.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from gideon.audio.sinks import EDGE_TTS_BYTES_PER_SECOND


class SimulatedTts:
    name = "simulated-tts"

    def __init__(self, first_byte_delay_s: float = 0.0, chars_per_second: float = 14.0) -> None:
        self.first_byte_delay_s = first_byte_delay_s
        self.chars_per_second = chars_per_second

    async def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        await asyncio.sleep(self.first_byte_delay_s)
        total = int(len(text) / self.chars_per_second * EDGE_TTS_BYTES_PER_SECOND)
        chunk = 1_440  # one edge-tts websocket audio message is about this size
        for start in range(0, max(total, chunk), chunk):
            yield bytes(min(chunk, max(total - start, 1)))
            await asyncio.sleep(0)
