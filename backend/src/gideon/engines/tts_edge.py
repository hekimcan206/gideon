"""edge-tts adapter: streams MP3 chunks from Microsoft Edge's online neural voices as they are synthesised."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

DEFAULT_VOICES: Mapping[str, str] = {"en": "en-GB-RyanNeural", "tr": "tr-TR-AhmetNeural"}


class EdgeTts:
    """Streaming TTS. Requires internet access; the answer text is sent to Microsoft's service (threat model).

    The first audio chunk typically arrives long before synthesis finishes, which is what makes the answer start
    quickly; the pipeline hands chunks to the sink as they arrive instead of waiting for the whole file.
    """

    def __init__(
        self, voices: Mapping[str, str] = DEFAULT_VOICES, rate: str = "+0%", chunk_timeout_s: float = 10.0
    ) -> None:
        self.chunk_timeout_s = chunk_timeout_s
        self.voices = dict(voices)
        self.rate = rate
        self.name = "edge-tts"

    async def stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        import edge_tts

        voice = self.voices.get(language) or self.voices["en"]
        communicate = edge_tts.Communicate(text, voice, rate=self.rate)
        stream = communicate.stream()
        while True:
            # A stalled connection must fail the interaction (and free the assistant), not hang it forever.
            try:
                chunk = await asyncio.wait_for(anext(stream), self.chunk_timeout_s)
            except StopAsyncIteration:
                return
            if chunk.get("type") == "audio" and chunk.get("data"):
                yield chunk["data"]
