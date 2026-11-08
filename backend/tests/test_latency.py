from __future__ import annotations

from pathlib import Path

import pytest

from gideon.latency import Mark, Trace, TraceLog, TraceOrderError, percentile, read_jsonl, summarize
from gideon.protocol import Outcome
from tests.fakes import FakeClock

MS = 1_000_000


def full_trace(clock: FakeClock, iid: str = "t1", stt_ms: float = 300) -> Trace:
    trace = Trace(iid, clock=clock)
    trace.mark(Mark.WAKE_AUDIO)
    clock.advance_ms(4)
    trace.mark(Mark.WAKE)
    trace.mark(Mark.SPEECH_END, clock.now + 1_500 * MS)  # audio-time mark supplied explicitly
    clock.advance_ms(1_500 + 560)
    trace.mark(Mark.ENDPOINT)
    clock.advance_ms(stt_ms)
    trace.mark(Mark.STT_DONE)
    clock.advance_ms(1)
    trace.mark(Mark.ROUTED)
    clock.advance_ms(400)
    trace.mark(Mark.TTS_FIRST_BYTE)
    clock.advance_ms(2)
    trace.mark(Mark.PLAYBACK_START)
    clock.advance_ms(1_000)
    trace.mark(Mark.DONE)
    trace.outcome = Outcome.OK
    return trace


def test_stages_are_gaps_between_consecutive_marks_and_add_up() -> None:
    trace = full_trace(FakeClock())
    stages = {s.stage: s.duration_ms for s in trace.stages()}
    assert stages == {
        "wake_detect": 4.0, "utterance": 1500.0, "endpoint": 560.0, "stt": 300.0, "route": 1.0,
        "tts_first_byte": 400.0, "playback_start": 2.0, "playback": 1000.0,
    }  # fmt: skip
    message = trace.to_message()
    assert message.speech_end_to_first_audio_ms == 560 + 300 + 1 + 400
    assert message.wake_to_first_audio_ms == 1500 + 560 + 300 + 1 + 400
    starts = [s.start_ms for s in message.stages]
    assert starts == sorted(starts) and starts[0] == 0.0


def test_first_record_of_a_mark_wins() -> None:
    clock = FakeClock()
    trace = Trace("t", clock=clock)
    first = trace.mark(Mark.TTS_FIRST_BYTE)
    clock.advance_ms(50)
    assert trace.mark(Mark.TTS_FIRST_BYTE) == first


def test_out_of_order_marks_are_an_instrumentation_error() -> None:
    clock = FakeClock()
    trace = Trace("t", clock=clock)
    trace.mark(Mark.STT_DONE)
    trace.mark(Mark.ENDPOINT, clock.now + 5 * MS)
    with pytest.raises(TraceOrderError):
        trace.validate()


def test_missing_marks_simply_drop_their_stages() -> None:
    clock = FakeClock()
    trace = Trace("typed", clock=clock)
    trace.mark(Mark.STT_DONE)
    clock.advance_ms(2)
    trace.mark(Mark.ROUTED)
    message = trace.to_message()
    assert [s.stage for s in message.stages] == ["route"]
    assert message.wake_to_first_audio_ms is None


@pytest.mark.parametrize(
    ("values", "pct", "expected"),
    [
        ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95, 10),
        ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50, 5),
        (list(range(1, 21)), 95, 19),
        ([42], 95, 42),
        ([5, 1, 3], 100, 5),
    ],
)
def test_percentile_is_nearest_rank(values: list[float], pct: float, expected: float) -> None:
    assert percentile(values, pct) == expected


def test_percentile_rejects_empty_input_and_bad_pct() -> None:
    with pytest.raises(ValueError, match="empty"):
        percentile([], 95)
    with pytest.raises(ValueError, match="pct"):
        percentile([1], 0)


def test_trace_log_round_trips_and_summary_uses_only_successful_runs(tmp_path: Path) -> None:
    clock = FakeClock()
    log = TraceLog(tmp_path / "sub" / "traces.jsonl")
    for i, stt in enumerate([100, 200, 300, 400]):
        log.write(full_trace(clock, f"ok{i}", stt_ms=stt))
    failed = full_trace(clock, "bad", stt_ms=9_999)
    failed.outcome = Outcome.ERROR
    log.write(failed)

    records = read_jsonl(tmp_path / "sub" / "traces.jsonl")
    assert len(records) == 5
    assert records[0]["marks_ns"]["wake"] - records[0]["marks_ns"]["wake_audio"] == 4 * MS

    stats = {s.stage: s for s in summarize(records)}
    assert stats["stt"].n == 4
    assert stats["stt"].median_ms == 250
    assert stats["stt"].p95_ms == 400
    assert stats["stt"].max_ms == 400  # the failed 9 999 ms run is excluded
    assert stats["speech_end_to_first_audio"].median_ms == 560 + 250 + 1 + 400
    assert list(stats)[-2:] == ["speech_end_to_first_audio", "wake_to_first_audio"]


def test_trace_log_without_path_is_a_no_op() -> None:
    record = TraceLog(None).write(full_trace(FakeClock()))
    assert record["outcome"] == "ok"
