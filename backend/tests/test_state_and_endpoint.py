from __future__ import annotations

import itertools

import pytest

from gideon.audio.endpoint import EndpointConfig, Endpointer, EndpointKind
from gideon.audio.vad import EnergyVad
from gideon.protocol import AssistantState as S
from gideon.state import TRANSITIONS, IllegalTransition, StateMachine
from tests.fakes import SILENCE, VOICED, script

ALLOWED = {
    (S.IDLE, S.LISTENING), (S.IDLE, S.THINKING),
    (S.LISTENING, S.THINKING), (S.LISTENING, S.IDLE),
    (S.THINKING, S.SPEAKING), (S.THINKING, S.IDLE), (S.THINKING, S.LISTENING),
    (S.SPEAKING, S.IDLE), (S.SPEAKING, S.LISTENING), (S.SPEAKING, S.THINKING),
}  # fmt: skip


@pytest.mark.parametrize(("source", "target"), list(itertools.permutations(S, 2)))
def test_transition_table_is_exactly_the_documented_one(source: S, target: S) -> None:
    machine = StateMachine(source)
    if (source, target) in ALLOWED:
        machine.transition(target)
        assert machine.state is target
    else:
        with pytest.raises(IllegalTransition):
            machine.transition(target)
        assert machine.state is source


def test_every_state_has_an_exit_and_idle_is_reachable_from_everywhere() -> None:
    for state, targets in TRANSITIONS.items():
        assert targets, state
        assert state is S.IDLE or S.IDLE in targets


def test_listeners_see_each_change_once_and_self_transitions_are_silent() -> None:
    seen: list[tuple[S, S]] = []
    machine = StateMachine()
    machine.subscribe(lambda a, b: seen.append((a, b)))
    machine.transition(S.LISTENING)
    machine.transition(S.LISTENING)
    machine.transition(S.THINKING)
    assert seen == [(S.IDLE, S.LISTENING), (S.LISTENING, S.THINKING)]


def feed(endpointer: Endpointer, spec: str) -> list[tuple[int, EndpointKind, int | None]]:
    events = []
    for i, pcm in enumerate(script(spec)):
        event = endpointer.feed(pcm, captured_at_ns=i)
        if event is not None:
            events.append((i, event.kind, event.last_voiced_ns))
            if event.kind is not EndpointKind.SPEECH_START:
                break
    return events


CFG = EndpointConfig(
    silence_ms=400, min_speech_ms=160, max_utterance_ms=2_000, no_speech_timeout_ms=800, start_grace_ms=0
)


def test_speech_end_fires_after_the_hangover_and_points_at_the_last_voiced_frame() -> None:
    events = feed(Endpointer(EnergyVad(), CFG), "SS VVVV SSSSS SS")
    assert events == [(3, EndpointKind.SPEECH_START, 3), (10, EndpointKind.SPEECH_END, 5)]


def test_short_pauses_inside_a_sentence_do_not_end_it() -> None:
    events = feed(Endpointer(EnergyVad(), CFG), "VVV SSSS VVV SSSSS")
    assert [e[1] for e in events] == [EndpointKind.SPEECH_START, EndpointKind.SPEECH_END]
    assert events[-1][2] == 9  # last voiced frame of the second word


def test_isolated_clicks_are_not_speech_and_time_out() -> None:
    events = feed(Endpointer(EnergyVad(), CFG), "V S V S V S V S V S")
    assert events == [(9, EndpointKind.NO_SPEECH, None)]


def test_endless_speech_is_cut_at_max_length() -> None:
    events = feed(Endpointer(EnergyVad(), CFG), "V" * 30)
    cuts = [e for e in events if e[1] is EndpointKind.MAX_LENGTH]
    assert cuts[0][0] == 24  # 25 frames * 80 ms = 2 000 ms


def test_reset_starts_a_fresh_utterance() -> None:
    endpointer = Endpointer(EnergyVad(), CFG)
    feed(endpointer, "VVV SSSSS")
    endpointer.reset()
    assert not endpointer.speech_started
    assert feed(endpointer, "SS VV") == [(3, EndpointKind.SPEECH_START, 3)]


def test_energy_vad_threshold() -> None:
    vad = EnergyVad()
    assert vad.is_speech(VOICED)
    assert not vad.is_speech(SILENCE)
    assert not vad.is_speech(SILENCE[:0])
    assert not vad.is_speech((VOICED // 300).astype(VOICED.dtype))  # ~ -63 dBFS


def test_wake_word_tail_inside_the_grace_window_does_not_start_the_utterance() -> None:
    """Regression from the benchmark: "...vis" + a long pause used to end an utterance before the command began."""
    cfg = EndpointConfig(silence_ms=400, min_speech_ms=160, no_speech_timeout_ms=4_000, start_grace_ms=240)
    # 3 voiced frames of wake-word tail, a 6-frame pause (longer than the hangover), then the command.
    events = feed(Endpointer(EnergyVad(), cfg), "VVV SSSSSS VVVV SSSSS")
    assert events == [(10, EndpointKind.SPEECH_START, 10), (17, EndpointKind.SPEECH_END, 12)]
    without_grace = feed(Endpointer(EnergyVad(), CFG), "VVV SSSSSS VVVV SSSSS")
    assert without_grace[-1] == (7, EndpointKind.SPEECH_END, 2)  # the bug: ends inside the pause
