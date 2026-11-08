from __future__ import annotations

import asyncio
from pathlib import Path

from gideon import protocol as p
from gideon.audio.sinks import SimulatedSpeakerSink
from gideon.latency import Mark, read_jsonl
from gideon.protocol import AssistantState as S
from gideon.protocol import Outcome
from tests.conftest import AssistantFactory, Recorder
from tests.fakes import FakeStt, FakeTts, ScriptSource


def states(recorder: Recorder) -> list[str]:
    return [m.state.value for m in recorder.of("state")]  # type: ignore[attr-defined]


async def test_happy_path_walks_every_state_and_traces_every_stage(
    make_assistant: AssistantFactory, recorder: Recorder, tmp_path: Path
) -> None:
    stt = FakeStt("Jarvis, what time is it?")
    assistant = make_assistant("SS W SS VVVVV SSSSSSSSS", stt=stt)
    await assistant.run()

    assert states(recorder) == ["listening", "thinking", "speaking", "idle"]
    kinds = recorder.types()
    for earlier, later in [("wake", "transcript"), ("transcript", "response"), ("response", "trace")]:
        assert kinds.index(earlier) < kinds.index(later)
    response = recorder.of("response")[0]
    assert (response.skill, response.intent, response.text) == ("clock", "time.now", "It's 14:05.")  # type: ignore[attr-defined]
    assert assistant.tts.spoken == [("It's 14:05.", "en")]  # type: ignore[attr-defined]

    trace = recorder.of("trace")[0]
    assert isinstance(trace, p.TraceMessage)
    assert trace.outcome is Outcome.OK
    assert [s.stage for s in trace.stages] == [
        "wake_detect", "utterance", "endpoint", "stt", "route", "tts_first_byte", "playback_start", "playback",
    ]  # fmt: skip
    assert trace.speech_end_to_first_audio_ms is not None and trace.speech_end_to_first_audio_ms >= 0

    # speech_end is the capture time of the last voiced frame, not the time the endpointer noticed.
    source = assistant.source
    assert isinstance(source, ScriptSource)
    last_voiced = source.released[9]  # S S W S S V V V V V -> index 9
    (record,) = read_jsonl(tmp_path / "traces.jsonl")
    assert record["marks_ns"][Mark.SPEECH_END.value] == last_voiced.captured_at_ns
    assert record["meta"]["trigger"] == "wake" and record["meta"]["transcript"] == "Jarvis, what time is it?"
    assert "trace_order_error" not in record["meta"]
    # The utterance handed to STT starts after the wake frame and ends at the endpoint.
    assert stt.calls == [1280 * (2 + 5 + 5)]


async def test_wake_word_is_ignored_while_listening_and_during_the_refractory_period(
    make_assistant: AssistantFactory, recorder: Recorder
) -> None:
    assistant = make_assistant("S W W VVVV SSSSSSSS")
    await assistant.run()
    assert len(recorder.of("wake")) == 1
    assert len(recorder.of("trace")) == 1


async def test_no_speech_after_wake_returns_to_idle(
    make_assistant: AssistantFactory, recorder: Recorder
) -> None:
    stt = FakeStt()
    assistant = make_assistant("S W " + "S" * 15, stt=stt)
    await assistant.run()
    assert states(recorder) == ["listening", "idle"]
    assert recorder.of("trace")[0].outcome is Outcome.NO_SPEECH  # type: ignore[attr-defined]
    assert stt.calls == []


async def test_empty_transcript_is_not_spoken(make_assistant: AssistantFactory, recorder: Recorder) -> None:
    tts = FakeTts()
    assistant = make_assistant(stt=FakeStt("  "), tts=tts)
    await assistant.run()
    assert recorder.of("trace")[0].outcome is Outcome.EMPTY_TRANSCRIPT  # type: ignore[attr-defined]
    assert tts.spoken == []
    assert assistant.state is S.IDLE


async def test_stt_failure_is_reported_and_the_assistant_recovers(
    make_assistant: AssistantFactory, recorder: Recorder
) -> None:
    assistant = make_assistant(
        "S W S VVVV SSSSSS SSSS W S VVVV SSSSSSSSS",
        stt=FakeStt(error=RuntimeError("model crashed")),
        refractory_ms=0,
    )
    await assistant.run()
    errors = recorder.of("error")
    assert len(errors) == 2 and errors[0].code == "pipeline_error"  # type: ignore[attr-defined]
    assert [t.outcome for t in recorder.of("trace")] == [Outcome.ERROR, Outcome.ERROR]  # type: ignore[attr-defined]
    assert assistant.state is S.IDLE


async def test_wake_word_while_speaking_barges_in(
    make_assistant: AssistantFactory, recorder: Recorder, tmp_path: Path
) -> None:
    # A long answer: 50 chunks, 20 ms apart. The second wake word arrives while it is being spoken.
    tts = FakeTts(chunks=50, chunk_delay_s=0.02)
    spec = "S W S VVVV SSSSSS" + "S" * 6 + "W S VVVV SSSSSSSS"
    assistant = make_assistant(
        spec, tts=tts, pause_after={15: 0.2}, stt=FakeStt("what time is it"), refractory_ms=0
    )
    await assistant.run()

    traces = recorder.of("trace")
    assert [t.outcome for t in traces] == [Outcome.BARGE_IN, Outcome.OK]  # type: ignore[attr-defined]
    wakes = recorder.of("wake")
    assert [w.barge_in for w in wakes] == [False, True]  # type: ignore[attr-defined]
    assert "speaking" in states(recorder)
    assert tts.chunks_sent < 100  # the first answer was cut off
    assert len(read_jsonl(tmp_path / "traces.jsonl")) == 2


async def test_typed_command_skips_audio_stages(make_assistant: AssistantFactory, recorder: Recorder) -> None:
    assistant = make_assistant("")
    await assistant.say("Saat kaç?", "tr")
    await assistant.drain()
    transcript = recorder.of("transcript")[0]
    assert transcript.source == "typed"  # type: ignore[attr-defined]
    assert recorder.of("response")[0].text == "Saat 14:05."  # type: ignore[attr-defined]
    trace = recorder.of("trace")[0]
    assert [s.stage for s in trace.stages] == ["route", "tts_first_byte", "playback_start", "playback"]  # type: ignore[attr-defined]
    assert states(recorder) == ["thinking", "speaking", "idle"]


async def test_push_to_talk_then_cancel(make_assistant: AssistantFactory, recorder: Recorder) -> None:
    assistant = make_assistant("")
    await assistant.push_to_talk()
    assert assistant.state is S.LISTENING
    await assistant.push_to_talk()  # idempotent while listening
    await assistant.cancel()
    await assistant.drain()
    assert assistant.state is S.IDLE
    assert recorder.of("trace")[0].outcome is Outcome.CANCELLED  # type: ignore[attr-defined]


async def test_cancel_while_speaking_stops_playback(
    make_assistant: AssistantFactory, recorder: Recorder
) -> None:
    tts = FakeTts(chunks=200, chunk_delay_s=0.01)
    assistant = make_assistant("", tts=tts)
    await assistant.say("what time is it")
    for _ in range(100):
        await asyncio.sleep(0.01)
        if assistant.state is S.SPEAKING:
            break
    assert assistant.state is S.SPEAKING
    await assistant.cancel()
    await assistant.drain()
    assert assistant.state is S.IDLE
    assert recorder.of("trace")[0].outcome is Outcome.CANCELLED  # type: ignore[attr-defined]
    assert tts.chunks_sent < 200


async def test_notification_is_spoken_only_when_idle(
    make_assistant: AssistantFactory, recorder: Recorder
) -> None:
    tts = FakeTts()
    assistant = make_assistant("", tts=tts)
    await assistant.notify("Your timer is done.", "en")
    await assistant.drain()
    assert tts.spoken == [("Your timer is done.", "en")]
    assert recorder.of("notification")[0].text == "Your timer is done."  # type: ignore[attr-defined]

    await assistant.push_to_talk()
    await assistant.notify("Second timer.", "en")
    await assistant.drain()
    assert len(tts.spoken) == 1  # busy listening: shown, not spoken
    assert len(recorder.of("notification")) == 2


async def test_simulated_speaker_reports_start_once_and_saves_audio(tmp_path: Path) -> None:
    sink = SimulatedSpeakerSink(realtime=True, bytes_per_second=60_000, save_dir=tmp_path)
    started: list[int] = []

    async def chunks():  # type: ignore[no-untyped-def]
        for _ in range(3):
            yield b"\x01" * 1_000
        yield b""

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await sink.play(chunks(), lambda: started.append(1))
    assert started == [1]
    assert loop.time() - t0 >= 0.045  # 3 000 bytes at 60 000 B/s
    assert (tmp_path / "answer-0001.mp3").read_bytes() == b"\x01" * 3_000


def test_engine_info(make_assistant: AssistantFactory) -> None:
    info = make_assistant("").engines()
    assert (info.wake, info.vad, info.stt, info.tts, info.audio) == (
        "fake-wake", "energy", "fake-stt", "fake-tts", "script",
    )  # fmt: skip


async def test_drain_returns_when_pending_tasks_already_finished(make_assistant: AssistantFactory) -> None:
    """Regression: awaiting finished tasks never yields, so done-callbacks could not empty the set (busy loop)."""
    assistant = make_assistant("")

    async def noop() -> None:
        return None

    task = asyncio.ensure_future(noop())
    await task
    assistant._pending.add(task)
    await asyncio.wait_for(assistant.drain(), 1)
    assert not assistant._pending
