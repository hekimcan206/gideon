"""Per-interaction latency accounting.

Every pipeline event is stamped with ``time.monotonic_ns()`` (never wall-clock time, which can jump). A
:class:`Trace` collects named marks for one interaction; stages are the gaps between consecutive marks, so the
waterfall always adds up to the end-to-end number instead of being measured separately and drifting apart.

Audio-time marks (``wake_audio``, ``speech_end``) are the capture timestamps of the frames involved, i.e. when a
microphone delivered (or the WAV source released) that 80 ms of audio. That is what makes "the user stopped
talking -> the first byte of the answer" a real user-perceived number rather than a processing-time number.
"""

from __future__ import annotations

import itertools
import json
import math
import statistics
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from gideon.protocol import Outcome, StageSpan, TraceMessage

Clock = Callable[[], int]


class Mark(StrEnum):
    WAKE_AUDIO = "wake_audio"  # capture time of the frame that pushed the wake score over the threshold
    WAKE = "wake"  # detector returned that score
    SPEECH_END = "speech_end"  # capture time of the last voiced frame of the command
    ENDPOINT = "endpoint"  # VAD decided the utterance is over (after the silence hangover)
    STT_DONE = "stt_done"
    ROUTED = "routed"  # intent matched and skill executed; response text ready
    TTS_FIRST_BYTE = "tts_first_byte"
    PLAYBACK_START = "playback_start"  # sink accepted the first audio for output
    DONE = "done"


# (stage name, from mark, to mark) in pipeline order. "utterance" is the user talking, not system latency; it is
# shown in the waterfall but excluded from speech_end -> first audio.
STAGES: tuple[tuple[str, Mark, Mark], ...] = (
    ("wake_detect", Mark.WAKE_AUDIO, Mark.WAKE),
    ("utterance", Mark.WAKE, Mark.SPEECH_END),
    ("endpoint", Mark.SPEECH_END, Mark.ENDPOINT),
    ("stt", Mark.ENDPOINT, Mark.STT_DONE),
    ("route", Mark.STT_DONE, Mark.ROUTED),
    ("tts_first_byte", Mark.ROUTED, Mark.TTS_FIRST_BYTE),
    ("playback_start", Mark.TTS_FIRST_BYTE, Mark.PLAYBACK_START),
    ("playback", Mark.PLAYBACK_START, Mark.DONE),
)


class TraceOrderError(ValueError):
    """A mark was recorded earlier than a mark that precedes it in the pipeline: an instrumentation bug."""


@dataclass
class Trace:
    interaction_id: str
    clock: Clock = time.monotonic_ns
    marks: dict[Mark, int] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    outcome: Outcome | None = None

    def mark(self, name: Mark, at_ns: int | None = None) -> int:
        """Record ``name`` now (or at a given monotonic timestamp). The first record of a mark wins."""
        if name in self.marks:
            return self.marks[name]
        value = self.clock() if at_ns is None else at_ns
        self.marks[name] = value
        return value

    def has(self, name: Mark) -> bool:
        return name in self.marks

    @property
    def origin_ns(self) -> int | None:
        return min(self.marks.values()) if self.marks else None

    def validate(self) -> None:
        order = [m for m in Mark if m in self.marks]
        for earlier, later in itertools.pairwise(order):
            if self.marks[later] < self.marks[earlier]:
                raise TraceOrderError(
                    f"{self.interaction_id}: {later} ({self.marks[later]}) precedes {earlier} ({self.marks[earlier]})"
                )

    def marks_ms(self) -> dict[str, float]:
        origin = self.origin_ns
        if origin is None:
            return {}
        return {m.value: _ms(self.marks[m] - origin) for m in Mark if m in self.marks}

    def stages(self) -> list[StageSpan]:
        origin = self.origin_ns
        if origin is None:
            return []
        spans: list[StageSpan] = []
        for name, start, end in STAGES:
            if start in self.marks and end in self.marks:
                spans.append(
                    StageSpan(
                        stage=name,
                        start_ms=_ms(self.marks[start] - origin),
                        duration_ms=_ms(max(0, self.marks[end] - self.marks[start])),
                    )
                )
        return spans

    def span_ms(self, start: Mark, end: Mark) -> float | None:
        if start in self.marks and end in self.marks:
            return _ms(self.marks[end] - self.marks[start])
        return None

    def to_message(self) -> TraceMessage:
        return TraceMessage(
            interaction_id=self.interaction_id,
            outcome=self.outcome or Outcome.ERROR,
            stages=self.stages(),
            marks_ms=self.marks_ms(),
            wake_to_first_audio_ms=self.span_ms(Mark.WAKE, Mark.TTS_FIRST_BYTE),
            speech_end_to_first_audio_ms=self.span_ms(Mark.SPEECH_END, Mark.TTS_FIRST_BYTE),
        )

    def to_record(self) -> dict[str, Any]:
        """JSONL record: absolute monotonic ns (for joining with logs) plus derived milliseconds."""
        message = self.to_message()
        return {
            "interaction_id": self.interaction_id,
            "outcome": message.outcome.value,
            "marks_ns": {m.value: self.marks[m] for m in Mark if m in self.marks},
            "stages_ms": {s.stage: s.duration_ms for s in message.stages},
            "wake_to_first_audio_ms": message.wake_to_first_audio_ms,
            "speech_end_to_first_audio_ms": message.speech_end_to_first_audio_ms,
            "meta": self.meta,
        }


class TraceLog:
    """Append-only JSONL file, one line per finished interaction. Lines are flushed so a crash keeps them."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, trace: Trace) -> dict[str, Any]:
        record = trace.to_record()
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@dataclass(frozen=True)
class StageStats:
    stage: str
    n: int
    median_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float

    def as_row(self) -> dict[str, float | int | str]:
        return {
            "stage": self.stage,
            "n": self.n,
            "median_ms": round(self.median_ms, 1),
            "p95_ms": round(self.p95_ms, 1),
            "min_ms": round(self.min_ms, 1),
            "max_ms": round(self.max_ms, 1),
        }


def percentile(values: Iterable[float], pct: float) -> float:
    """Nearest-rank percentile: always an observed value, never an interpolation between two runs."""
    data = sorted(values)
    if not data:
        raise ValueError("percentile of an empty sequence")
    if not 0 < pct <= 100:
        raise ValueError("pct must be in (0, 100]")
    rank = math.ceil(pct / 100 * len(data))
    return data[rank - 1]


def summarize(records: Iterable[Mapping[str, Any]], *, only_ok: bool = True) -> list[StageStats]:
    """Median/p95 per stage plus the two end-to-end totals, over successful interactions by default."""
    columns: dict[str, list[float]] = {}
    for rec in records:
        if only_ok and rec.get("outcome") != Outcome.OK.value:
            continue
        for stage, value in rec.get("stages_ms", {}).items():
            columns.setdefault(stage, []).append(float(value))
        for total in ("speech_end_to_first_audio_ms", "wake_to_first_audio_ms"):
            if rec.get(total) is not None:
                columns.setdefault(total.removesuffix("_ms"), []).append(float(rec[total]))
    order = [s[0] for s in STAGES] + ["speech_end_to_first_audio", "wake_to_first_audio"]
    result = []
    for stage in order:
        values = columns.get(stage)
        if values:
            result.append(
                StageStats(
                    stage=stage,
                    n=len(values),
                    median_ms=statistics.median(values),
                    p95_ms=percentile(values, 95),
                    min_ms=min(values),
                    max_ms=max(values),
                )
            )
    return result


def _ms(ns: int) -> float:
    return round(ns / 1_000_000, 3)
