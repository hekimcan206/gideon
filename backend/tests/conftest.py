from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from gideon.audio.endpoint import EndpointConfig
from gideon.audio.sinks import SimulatedSpeakerSink
from gideon.audio.vad import EnergyVad
from gideon.latency import TraceLog
from gideon.pipeline import Assistant, PipelineConfig
from gideon.skills.base import SkillContext
from gideon.skills.builtin import ClockSkill, NotesSkill, NoteStore, OpenAppSkill, TimerSkill
from gideon.skills.router import SkillRouter
from tests.constants import FIXED_NOW
from tests.fakes import FakeStt, FakeTts, FakeWake, ScriptSource, script


class Recorder:
    def __init__(self) -> None:
        self.messages: list[BaseModel] = []

    def emit(self, message: BaseModel) -> None:
        self.messages.append(message)

    def types(self) -> list[str]:
        return [getattr(m, "type", "?") for m in self.messages]

    def of(self, kind: str) -> list[BaseModel]:
        return [m for m in self.messages if getattr(m, "type", None) == kind]


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def router(tmp_path: Path) -> SkillRouter:
    return SkillRouter(
        [
            ClockSkill(),
            TimerSkill(),
            NotesSkill(NoteStore(tmp_path / "notes.jsonl")),
            OpenAppSkill(dry_run=True),
        ]
    )


AssistantFactory = Callable[..., Assistant]


@pytest.fixture
def make_assistant(recorder: Recorder, router: SkillRouter, tmp_path: Path) -> AssistantFactory:
    def factory(
        spec: str = "SS W SS VVVVV SSSSSSSSS",
        *,
        stt: FakeStt | None = None,
        tts: FakeTts | None = None,
        pause_after: dict[int, float] | None = None,
        sink: SimulatedSpeakerSink | None = None,
        endpoint: EndpointConfig | None = None,
        refractory_ms: int = 1_500,
    ) -> Assistant:
        return Assistant(
            source=ScriptSource(script(spec), clock=time.monotonic_ns, pause_after=pause_after),
            wake=FakeWake(),
            vad=EnergyVad(),
            stt=stt or FakeStt(),
            tts=tts or FakeTts(),
            sink=sink or SimulatedSpeakerSink(realtime=False),
            router=router,
            config=PipelineConfig(
                wake_refractory_ms=refractory_ms,
                endpoint=endpoint
                or EndpointConfig(silence_ms=400, min_speech_ms=160, no_speech_timeout_ms=800),
            ),
            emit=recorder.emit,
            trace_log=TraceLog(tmp_path / "traces.jsonl"),
            skill_context=SkillContext(now=lambda: FIXED_NOW),
        )

    return factory
