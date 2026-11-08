"""Assistant state machine.

The UI renders these four states, and the pipeline's concurrency (capture keeps running while a response is being
produced) makes illegal jumps easy to introduce. Making the transition table explicit turns such a bug into an
immediate exception in tests instead of a UI that shows "speaking" forever.
"""

from __future__ import annotations

from collections.abc import Callable

from gideon.protocol import AssistantState as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.IDLE: frozenset({S.LISTENING, S.THINKING}),  # wake word / push-to-talk, typed command
    S.LISTENING: frozenset({S.THINKING, S.IDLE}),  # end of utterance, no speech
    S.THINKING: frozenset({S.SPEAKING, S.IDLE, S.LISTENING}),  # answer, nothing to say/error, barge-in
    S.SPEAKING: frozenset({S.IDLE, S.LISTENING, S.THINKING}),  # done, barge-in, typed command interrupts
}


class IllegalTransition(RuntimeError):
    pass


Listener = Callable[[S, S], None]


class StateMachine:
    def __init__(self, initial: S = S.IDLE) -> None:
        self._state = initial
        self._listeners: list[Listener] = []

    @property
    def state(self) -> S:
        return self._state

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def can(self, target: S) -> bool:
        return target in TRANSITIONS[self._state]

    def transition(self, target: S) -> None:
        if target == self._state:
            return
        if not self.can(target):
            raise IllegalTransition(f"{self._state} -> {target}")
        previous, self._state = self._state, target
        for listener in self._listeners:
            listener(previous, target)
