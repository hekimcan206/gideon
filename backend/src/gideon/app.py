"""Composition root: builds engines, skills and the server from :class:`~gideon.config.Settings`."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from gideon.audio.endpoint import EndpointConfig
from gideon.audio.interfaces import AudioSink, AudioSource, TextToSpeech, VoiceActivityDetector
from gideon.audio.simulated import SimulatedTts
from gideon.audio.sinks import SimulatedSpeakerSink
from gideon.audio.sources import WavSource
from gideon.audio.vad import EnergyVad
from gideon.config import Settings
from gideon.latency import TraceLog
from gideon.pipeline import Assistant, PipelineConfig
from gideon.server import GideonServer, Hub
from gideon.skills.base import Skill
from gideon.skills.builtin import (
    ClockSkill,
    HelpSkill,
    NotesSkill,
    NoteStore,
    OpenAppSkill,
    SystemInfoSkill,
    TimerSkill,
    load_allow_list,
)
from gideon.skills.llm import LlmFallbackSkill, OllamaProvider
from gideon.skills.router import SkillRouter

if TYPE_CHECKING:
    from gideon.engines.stt_faster_whisper import FasterWhisperStt

log = logging.getLogger(__name__)


def build_router(settings: Settings, *, notes_path: Path | None = None) -> SkillRouter:
    # Order matters where phrasings overlap: "start a timer" must reach the timer skill before "start <app>".
    skills: Sequence[Skill] = (
        ClockSkill(),
        TimerSkill(),
        NotesSkill(NoteStore(notes_path or settings.data_dir / "notes.jsonl")),
        SystemInfoSkill(),
        HelpSkill(),
        OpenAppSkill(load_allow_list(settings.apps_file), dry_run=settings.apps_dry_run),
    )
    fallback = None
    if settings.llm == "ollama":
        fallback = LlmFallbackSkill(OllamaProvider(settings.ollama_url, settings.ollama_model))
    return SkillRouter(skills, fallback)


def build_vad(settings: Settings) -> VoiceActivityDetector:
    if settings.vad == "webrtc":
        from gideon.engines.vad_webrtc import WebRtcVad

        return WebRtcVad(settings.vad_aggressiveness)
    return EnergyVad()


def build_tts(settings: Settings) -> TextToSpeech:
    if settings.tts == "edge":
        from gideon.engines.tts_edge import EdgeTts

        return EdgeTts({"en": settings.voice_en, "tr": settings.voice_tr})
    return SimulatedTts(first_byte_delay_s=0.15)


def build_io(settings: Settings) -> tuple[AudioSource, AudioSink]:
    if settings.audio == "wav":
        paths = sorted(settings.wav_dir.glob("*.wav"))
        if not paths:
            raise SystemExit(f"no WAV files in {settings.wav_dir}; run `python -m gideon fixtures` first")
        return WavSource(paths, loop=settings.wav_loop, gap_s=settings.wav_gap_s), SimulatedSpeakerSink()
    from gideon.engines.live_audio import MicrophoneSource, SpeakerSink

    return MicrophoneSource(), SpeakerSink()


def build_stt(settings: Settings, router: SkillRouter) -> FasterWhisperStt:
    from gideon.engines import stt_faster_whisper as fw

    languages = [settings.stt_language] if settings.stt_language else ["en", "tr"]
    prompts = {lang: router.vocabulary(lang) for lang in languages} if settings.stt_prompt else None
    stt = fw.FasterWhisperStt(
        settings.stt_model,
        compute_type=settings.stt_compute_type,
        cpu_threads=settings.stt_threads,
        language=settings.stt_language,
        prompts=prompts,
    )
    stt.warm_up()
    return stt


def build_assistant(settings: Settings, hub: Hub) -> Assistant:
    from gideon.engines.wake_openwakeword import OpenWakeWordDetector

    source, sink = build_io(settings)
    router = build_router(settings)
    stt = build_stt(settings, router)
    return Assistant(
        source=source,
        wake=OpenWakeWordDetector(settings.wake_model),
        vad=build_vad(settings),
        stt=stt,
        tts=build_tts(settings),
        sink=sink,
        router=router,
        config=PipelineConfig(
            wake_threshold=settings.wake_threshold,
            endpoint=EndpointConfig(silence_ms=settings.endpoint_silence_ms),
        ),
        emit=hub.emit,
        trace_log=TraceLog(settings.trace_path),
    )


async def serve(settings: Settings) -> None:
    hub = Hub()
    assistant = await asyncio.to_thread(build_assistant, settings, hub)
    server = GideonServer(
        assistant,
        hub,
        host=settings.host,
        port=settings.port,
        allowed_origins=settings.allowed_origins,
        token=settings.token,
    )
    await server.start()
    stop = asyncio.Event()
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    log.info(
        "ready", extra={"fields": {"engines": assistant.engines().model_dump(), "audio": settings.audio}}
    )
    pipeline = asyncio.create_task(assistant.run(), name="pipeline")
    stopper = asyncio.create_task(stop.wait(), name="stop")
    try:
        await asyncio.wait({pipeline, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        pipeline.cancel()
        stopper.cancel()
        await server.close()
