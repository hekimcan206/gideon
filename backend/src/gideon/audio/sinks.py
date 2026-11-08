"""Audio sinks that need no audio hardware."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

# edge-tts streams 24 kHz mono MP3 at 48 kbit/s, i.e. 6 000 bytes per second of speech.
EDGE_TTS_BYTES_PER_SECOND = 6_000


class SimulatedSpeakerSink:
    """Stands in for a speaker in simulated-audio mode.

    Playback "starts" when the first chunk arrives, and the sink then waits for as long as the audio would take to
    play, so the UI shows a realistic *speaking* phase and barge-in can be exercised without a sound card. The
    answer can optionally be written to disk for inspection.
    """

    name = "simulated-speaker"

    def __init__(
        self,
        *,
        bytes_per_second: int = EDGE_TTS_BYTES_PER_SECOND,
        realtime: bool = True,
        save_dir: Path | None = None,
    ) -> None:
        self.bytes_per_second = bytes_per_second
        self.realtime = realtime
        self.save_dir = save_dir
        self.played: list[bytes] = []
        self._counter = 0

    async def play(self, chunks: AsyncIterator[bytes], on_started: Callable[[], None]) -> None:
        buffer = bytearray()
        loop = asyncio.get_running_loop()
        started_at: float | None = None
        async for chunk in chunks:
            if not chunk:
                continue
            if started_at is None:
                started_at = loop.time()
                on_started()
            buffer.extend(chunk)
        if started_at is not None and self.realtime:
            remaining = len(buffer) / self.bytes_per_second - (loop.time() - started_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
        self.played.append(bytes(buffer))
        if self.save_dir is not None and buffer:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            self._counter += 1
            (self.save_dir / f"answer-{self._counter:04d}.mp3").write_bytes(bytes(buffer))
