"""The voice pipeline: capture -> wake word -> end-of-utterance -> STT -> skills -> streaming TTS -> playback.

Concurrency model (single asyncio loop):

* the **capture loop** consumes one 80 ms frame at a time and never blocks on a slow stage. It always feeds the
  wake-word detector (keeping its rolling feature window current), and while *listening* also feeds the
  endpointer;
* when the utterance ends, a **response task** runs STT (in a worker thread), routing, TTS and playback, while
  capture keeps going. A wake word heard during *thinking* or *speaking* cancels that task (barge-in): playback
  stops within one chunk and a new interaction starts listening immediately.

Every step stamps the interaction's :class:`~gideon.latency.Trace`; finished traces go to the JSONL log and to
the UI as a ``trace`` message for the latency waterfall.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from pydantic import BaseModel

from gideon import protocol as p
from gideon.audio.endpoint import EndpointConfig, Endpointer, EndpointKind
from gideon.audio.interfaces import (
    AudioFrame,
    AudioSink,
    AudioSource,
    Pcm,
    SpeechToText,
    TextToSpeech,
    Transcription,
    VoiceActivityDetector,
    WakeWordDetector,
)
from gideon.latency import Clock, Mark, Trace, TraceLog, TraceOrderError
from gideon.protocol import AssistantState as S
from gideon.protocol import Outcome
from gideon.skills.base import SkillContext
from gideon.skills.router import SkillRouter
from gideon.state import StateMachine

log = logging.getLogger(__name__)

# Emission is synchronous on purpose: broadcasting only enqueues frames on each connection, and a plain call
# keeps messages in exactly the order the pipeline produced them (state before trace, wake before transcript).
Emit = Callable[[BaseModel], None]


def _discard(_message: BaseModel) -> None:
    return None


@dataclass(frozen=True)
class PipelineConfig:
    wake_threshold: float = 0.5
    # After a detection the score stays high for several frames; ignore re-triggers for this long.
    wake_refractory_ms: int = 1_500
    endpoint: EndpointConfig = field(default_factory=EndpointConfig)


@dataclass
class _Interaction:
    trace: Trace
    frames: list[Pcm] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    finished: bool = False


class Assistant:
    def __init__(
        self,
        *,
        source: AudioSource,
        wake: WakeWordDetector,
        vad: VoiceActivityDetector,
        stt: SpeechToText,
        tts: TextToSpeech,
        sink: AudioSink,
        router: SkillRouter,
        config: PipelineConfig | None = None,
        emit: Emit = _discard,
        trace_log: TraceLog | None = None,
        skill_context: SkillContext | None = None,
        clock: Clock = time.monotonic_ns,
    ) -> None:
        self.source = source
        self.wake = wake
        self.vad = vad
        self.stt = stt
        self.tts = tts
        self.sink = sink
        self.router = router
        self.config = config or PipelineConfig()
        self.emit = emit
        self.trace_log = trace_log or TraceLog(None)
        self.clock = clock
        self.ctx = skill_context or SkillContext()
        self.ctx.notify = self.notify
        self.machine = StateMachine()
        self.machine.subscribe(self._on_state)
        self.endpointer = Endpointer(vad, self.config.endpoint)
        self.records: list[dict[str, object]] = []
        self._current: _Interaction | None = None
        self._refractory_until = 0
        self._pending: set[asyncio.Task[None]] = set()
        self._finished = asyncio.Event()

    # ------------------------------------------------------------------------------------------------ public

    @property
    def state(self) -> S:
        return self.machine.state

    def engines(self) -> p.EngineInfo:
        return p.EngineInfo(
            wake=self.wake.name,
            vad=self.vad.name,
            stt=self.stt.name,
            tts=self.tts.name,
            audio=self.source.name,
        )

    async def run(self) -> None:
        """Consume the source until it ends, then let the last response finish."""
        try:
            async for frame in self.source.frames():
                await self.on_frame(frame)
        finally:
            await self.drain()
            self._finished.set()

    async def drain(self) -> None:
        while self._pending:
            batch = list(self._pending)
            await asyncio.gather(*batch, return_exceptions=True)
            # Remove explicitly: awaiting already-finished tasks does not yield to the loop, so the done-callbacks
            # that would discard them never run and a plain `while self._pending` spins forever.
            self._pending.difference_update(batch)

    async def on_frame(self, frame: AudioFrame) -> None:
        score = self.wake.process(frame.pcm)
        state = self.state
        if state is S.LISTENING:
            self._listen(frame)
            return
        if score >= self.config.wake_threshold and frame.captured_at_ns >= self._refractory_until:
            await self._on_wake(frame, score)

    async def push_to_talk(self) -> None:
        if self.state is S.LISTENING:
            return
        await self._interrupt()
        interaction = self._start_interaction()
        self.endpointer.reset()
        interaction.trace.meta["trigger"] = "ptt"
        self.machine.transition(S.LISTENING)

    async def say(self, text: str, language: str | None = None) -> None:
        """Typed command: same skills, TTS and trace, without wake word/VAD/STT."""
        await self._interrupt()
        interaction = self._start_interaction()
        interaction.trace.meta["trigger"] = "typed"
        self.machine.transition(S.THINKING)
        transcript = Transcription(text=text, language=language or "en")
        self._spawn(interaction, self._respond(interaction, transcript=transcript))

    async def cancel(self) -> None:
        if self.state in (S.THINKING, S.SPEAKING, S.LISTENING):
            await self._interrupt(outcome=Outcome.CANCELLED)
            self.machine.transition(S.IDLE)

    async def notify(self, text: str, language: str) -> None:
        """Unprompted announcement (timer). Always shown in the UI; spoken only if the assistant is idle."""
        self.emit(p.NotificationMessage(text=text, language=language))
        if self.state is S.IDLE:
            task = asyncio.create_task(self._speak_unprompted(text, language))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    # ----------------------------------------------------------------------------------------------- capture

    async def _on_wake(self, frame: AudioFrame, score: float) -> None:
        barge_in = self.state in (S.THINKING, S.SPEAKING)
        await self._interrupt(outcome=Outcome.BARGE_IN)
        interaction = self._start_interaction()
        trace = interaction.trace
        trace.mark(Mark.WAKE_AUDIO, frame.captured_at_ns)
        trace.mark(Mark.WAKE)
        trace.meta.update(trigger="wake", wake_score=round(score, 3), barge_in=barge_in)
        self._refractory_until = frame.captured_at_ns + self.config.wake_refractory_ms * 1_000_000
        self.wake.reset()
        self.endpointer.reset()
        self.machine.transition(S.LISTENING)
        log.info("wake", extra={"fields": {"interaction": trace.interaction_id, "score": round(score, 3)}})
        self.emit(
            p.WakeDetected(
                interaction_id=trace.interaction_id,
                model=self.wake.name,
                score=min(score, 1.0),
                barge_in=barge_in,
            )
        )

    def _listen(self, frame: AudioFrame) -> None:
        interaction = self._current
        if interaction is None:  # defensive: LISTENING always has an interaction
            self.machine.transition(S.IDLE)
            return
        interaction.frames.append(frame.pcm)
        event = self.endpointer.feed(frame.pcm, frame.captured_at_ns)
        if event is None or event.kind is EndpointKind.SPEECH_START:
            return
        trace = interaction.trace
        if event.kind is EndpointKind.NO_SPEECH:
            trace.outcome = Outcome.NO_SPEECH
            self.machine.transition(S.IDLE)
            self._spawn(interaction, self._finish(interaction))
            return
        if event.last_voiced_ns is not None:
            trace.mark(Mark.SPEECH_END, event.last_voiced_ns)
        trace.mark(Mark.ENDPOINT)
        trace.meta["endpoint"] = event.kind.value
        pcm = np.concatenate(interaction.frames)
        interaction.frames = []
        self.machine.transition(S.THINKING)
        self._spawn(interaction, self._respond(interaction, pcm=pcm))

    # ---------------------------------------------------------------------------------------------- response

    async def _respond(
        self, interaction: _Interaction, *, pcm: Pcm | None = None, transcript: Transcription | None = None
    ) -> None:
        trace = interaction.trace
        iid = trace.interaction_id
        try:
            if transcript is None:
                assert pcm is not None
                transcript = await self.stt.transcribe(pcm)
                trace.mark(Mark.STT_DONE)
                trace.meta.update(
                    audio_ms=round(len(pcm) / 16),
                    stt_confidence=round(transcript.confidence, 3),
                )
                source: Literal["speech", "typed"] = "speech"
            else:
                trace.mark(Mark.STT_DONE)
                source = "typed"
            trace.meta.update(transcript=transcript.text, stt_language=transcript.language)
            self.emit(
                p.TranscriptMessage(
                    interaction_id=iid, text=transcript.text, language=transcript.language, source=source
                )
            )
            if not transcript.text.strip():
                trace.outcome = Outcome.EMPTY_TRANSCRIPT
                return

            routed = await self.router.route(transcript.text, transcript.language, self.ctx)
            trace.mark(Mark.ROUTED)
            trace.meta.update(skill=routed.skill, intent=routed.intent, language=routed.response.language)
            self.emit(
                p.ResponseMessage(
                    interaction_id=iid,
                    skill=routed.skill,
                    intent=routed.intent,
                    text=routed.response.text,
                    language=routed.response.language,
                )
            )
            await self._speak(trace, routed.response.text, routed.response.language)
            trace.outcome = Outcome.OK
        except asyncio.CancelledError:
            trace.outcome = trace.outcome or Outcome.BARGE_IN
            raise
        except Exception as exc:
            log.exception("interaction failed", extra={"fields": {"interaction": iid}})
            trace.outcome = Outcome.ERROR
            self.emit(p.ErrorMessage(code="pipeline_error", message=type(exc).__name__, interaction_id=iid))
        finally:
            if not interaction.finished:
                await asyncio.shield(self._finish(interaction))

    async def _speak(self, trace: Trace, text: str, language: str) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            async for chunk in self.tts.stream(text, language):
                if chunk:
                    trace.mark(Mark.TTS_FIRST_BYTE)
                    yield chunk

        def started() -> None:
            trace.mark(Mark.PLAYBACK_START)
            if self._current is not None and self._current.trace is trace and self.machine.can(S.SPEAKING):
                self.machine.transition(S.SPEAKING)

        await self.sink.play(chunks(), started)

    async def _speak_unprompted(self, text: str, language: str) -> None:
        if self.state is not S.IDLE:
            return
        interaction = self._start_interaction()
        interaction.trace.meta["trigger"] = "notification"
        self.machine.transition(S.THINKING)
        interaction.task = asyncio.current_task()
        try:
            await self._speak(interaction.trace, text, language)
            interaction.trace.outcome = Outcome.OK
        except asyncio.CancelledError:
            interaction.trace.outcome = interaction.trace.outcome or Outcome.BARGE_IN
            raise
        finally:
            if not interaction.finished:
                await asyncio.shield(self._finish(interaction))

    async def _finish(self, interaction: _Interaction) -> None:
        if interaction.finished:
            return
        interaction.finished = True
        trace = interaction.trace
        trace.mark(Mark.DONE)
        trace.outcome = trace.outcome or Outcome.ERROR
        try:
            trace.validate()
        except TraceOrderError as exc:  # an instrumentation bug must not take the assistant down
            log.error("trace order", extra={"fields": {"error": str(exc)}})
            trace.meta["trace_order_error"] = str(exc)
        self.records.append(self.trace_log.write(trace))
        if self._current is interaction:
            self._current = None
            if self.state is not S.IDLE:
                self.machine.transition(S.IDLE)
        log.info(
            "interaction",
            extra={
                "fields": {
                    "interaction": trace.interaction_id,
                    "outcome": trace.outcome.value,
                    "stages_ms": {s.stage: s.duration_ms for s in trace.stages()},
                }
            },
        )
        self.emit(trace.to_message())

    # ------------------------------------------------------------------------------------------------ helpers

    def _start_interaction(self) -> _Interaction:
        interaction = _Interaction(trace=Trace(uuid.uuid4().hex[:12], clock=self.clock))
        self._current = interaction
        return interaction

    def _spawn(self, interaction: _Interaction, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        interaction.task = task
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _interrupt(self, outcome: Outcome = Outcome.BARGE_IN) -> None:
        """Cancel the interaction in flight (if any) and wait until it has written its trace."""
        current = self._current
        if current is None:
            return
        current.trace.outcome = current.trace.outcome or outcome
        task = current.task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if not current.finished:
            await self._finish(current)
        self._current = None

    def _on_state(self, previous: S, current: S) -> None:
        iid = self._current.trace.interaction_id if self._current else None
        self.emit(p.StateChanged(state=current, previous=previous, interaction_id=iid))
