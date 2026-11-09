"""faster-whisper (CTranslate2) adapter, CPU int8 by default (see docs/adr/0002)."""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from gideon.audio.interfaces import SAMPLE_RATE, Pcm, Transcription


@dataclass(frozen=True, slots=True)
class Decoding:
    language: str
    text: str
    avg_logprob: float


def pick_best(decodings: Sequence[Decoding]) -> Decoding:
    """The decoding the model is most confident in (highest mean token log-probability); empty text loses."""
    if not decodings:
        raise ValueError("no decodings")
    return max(decodings, key=lambda d: (bool(d.text), d.avg_logprob))


class FasterWhisperStt:
    """Transcribes one endpointed utterance.

    Commands are 1-4 s long, so the whole utterance is transcribed once after the endpoint; Whisper pads every
    input to a 30 s window anyway, so re-decoding a growing buffer for partial results would multiply the cost
    (ADR 0002).

    Language. Whisper's language-ID head is unreliable on two-word clips: in our fixtures it labelled "Saat kaç?"
    Romanian and "Notlarımı oku" Portuguese, with en/tr probabilities a few hundredths apart. Detection also costs
    a full encoder pass. So with no fixed language the utterance is decoded *in every supported language in
    parallel* (one CTranslate2 worker each; CTranslate2 releases the GIL) and the decoding with the highest mean
    log-probability wins. It picked the right language on 45/45 fixture decodes; the price is about 1.7x the
    wall time of a single decode on a 16-thread laptop CPU (ADR 0002). With ``language`` set, a single decode runs.

    Vocabulary prompts. Each language's decode is primed with example phrases of the installed skills
    (``initial_prompt``), which biases Whisper toward the command vocabulary ("kur", not "kor"). Disable with
    ``prompts=None`` to measure the effect (docs/benchmarks.md reports both).
    """

    def __init__(
        self,
        model: str = "base",
        *,
        compute_type: str = "int8",
        cpu_threads: int | None = None,
        language: str | None = None,
        allowed_languages: Sequence[str] = ("en", "tr"),
        prompts: Mapping[str, str] | None = None,
        beam_size: int = 1,
        vad_filter: bool = True,
        download_root: str | None = None,
    ) -> None:
        from faster_whisper import WhisperModel

        self.language = language
        self.languages = (language,) if language else tuple(allowed_languages)
        cores = os.cpu_count() or 4
        # Physical cores (logical / 2) split across the parallel decodes. Measured: 8 threads x 2 decodes on a
        # 16-thread CPU is slower than 4 x 2 (oversubscription; docs/benchmarks.md).
        threads = (
            cpu_threads if cpu_threads is not None else max(2, min(8, cores // 2 // len(self.languages)))
        )
        self._model: Any = WhisperModel(
            model,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=threads,
            num_workers=len(self.languages),
            download_root=download_root,
        )
        self.prompts = dict(prompts or {})
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        mode = language or "+".join(self.languages)
        self.name = f"faster-whisper:{model}:{compute_type}:{mode}:t{threads}x{len(self.languages)}"
        self.last_decodings: list[Decoding] = []

    def warm_up(self) -> None:
        """First inference allocates buffers and is several times slower; pay that at start-up, not on a user."""
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(SAMPLE_RATE) * 300).astype(np.int16)
        audio = noise.astype(np.float32) / 32768.0
        for language in self.languages:
            self._decode(audio, language)

    async def transcribe(self, pcm: Pcm) -> Transcription:
        audio = pcm.astype(np.float32) / 32768.0
        decodings = await asyncio.gather(
            *(asyncio.to_thread(self._decode, audio, lang) for lang in self.languages)
        )
        self.last_decodings = list(decodings)
        best = pick_best(decodings)
        confidence = math.exp(best.avg_logprob) if best.text else 0.0
        return Transcription(text=best.text, language=best.language, confidence=confidence)

    def _decode(self, audio: Any, language: str) -> Decoding:
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            vad_parameters={"min_silence_duration_ms": 300},
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=self.prompts.get(language),
        )
        decoded = list(segments)  # the generator does the decoding
        text = "".join(segment.text for segment in decoded).strip()
        logprob = float(np.mean([s.avg_logprob for s in decoded])) if decoded else float("-inf")
        return Decoding(language=language, text=text, avg_logprob=logprob)
