"""Latency benchmark: the real pipeline (openWakeWord, WebRTC VAD, faster-whisper, edge-tts) on the WAV fixtures.

Each fixture is replayed in real time (80 ms frames released on the capture schedule), so wake detection, the
endpoint hangover and every later stage see audio exactly when a microphone would deliver it. One fresh
``Assistant`` per utterance (engines are shared and stay warm), N rounds over all fixtures. The output is the JSONL
trace of every interaction plus a per-stage summary (median / p95, nearest-rank).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import platform
import tempfile
import time
from pathlib import Path
from typing import Any

from gideon.audio.endpoint import EndpointConfig
from gideon.audio.interfaces import SpeechToText, TextToSpeech, VoiceActivityDetector, WakeWordDetector
from gideon.audio.sinks import SimulatedSpeakerSink
from gideon.audio.sources import WavSource
from gideon.config import Settings
from gideon.fixtures import load_manifest
from gideon.latency import TraceLog, percentile, summarize
from gideon.pipeline import Assistant, PipelineConfig
from gideon.protocol import AssistantState


async def run_case(
    wav: Path,
    *,
    wake: WakeWordDetector,
    vad: VoiceActivityDetector,
    stt: SpeechToText,
    tts: TextToSpeech,
    settings: Settings,
    notes_path: Path,
    trace_log: TraceLog,
    realtime: bool = True,
) -> dict[str, Any] | None:
    """Replay one fixture; stop as soon as its interaction has finished. Returns the trace record (or None)."""
    from gideon.app import build_router

    wake.reset()
    assistant = Assistant(
        source=WavSource([wav], realtime=realtime, gap_s=0.5, tail_s=6.0),
        wake=wake,
        vad=vad,
        stt=stt,
        tts=tts,
        sink=SimulatedSpeakerSink(realtime=False),
        router=build_router(settings, notes_path=notes_path),
        config=PipelineConfig(
            wake_threshold=settings.wake_threshold,
            endpoint=EndpointConfig(silence_ms=settings.endpoint_silence_ms),
        ),
        trace_log=trace_log,
    )
    async for frame in assistant.source.frames():
        await assistant.on_frame(frame)
        if assistant.records and assistant.state is AssistantState.IDLE:
            break
    await assistant.drain()
    for skill in assistant.router.skills:  # stop timers the fixtures set
        for task in list(getattr(skill, "_tasks", ())):
            task.cancel()
    return assistant.records[0] if assistant.records else None


async def run(fixtures: Path, out_dir: Path, runs: int, settings: Settings, label: str) -> dict[str, Any]:
    settings = dataclasses.replace(settings, apps_dry_run=True)  # never launch real apps from a benchmark
    from gideon.app import build_router, build_stt, build_tts, build_vad
    from gideon.engines.wake_openwakeword import OpenWakeWordDetector

    cases = load_manifest(fixtures)
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / f"traces-{label}.jsonl"
    trace_path.unlink(missing_ok=True)
    trace_log = TraceLog(trace_path)

    t0 = time.perf_counter()
    stt = build_stt(settings, build_router(settings))  # loads and warms up the model
    load_s = time.perf_counter() - t0
    wake = OpenWakeWordDetector(settings.wake_model)
    vad = build_vad(settings)
    tts = build_tts(settings)

    records: list[dict[str, Any]] = []
    correct = detected = language_ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        for round_no in range(runs):
            for case in cases:
                record = await run_case(
                    fixtures / case["file"],
                    wake=wake,
                    vad=vad,
                    stt=stt,
                    tts=tts,
                    settings=settings,
                    notes_path=Path(tmp) / "notes.jsonl",
                    trace_log=trace_log,
                )
                if record is None:
                    print(f"[{round_no}] {case['name']}: no interaction (wake word missed)")
                    continue
                detected += 1
                meta = record["meta"]
                ok = meta.get("skill") == case["skill"] and meta.get("intent") == case["intent"]
                language_ok += meta.get("language") == case["language"]
                correct += ok
                record["case"] = case["name"]
                records.append(record)
                print(
                    f"[{round_no}] {case['name']:<10} {record['outcome']:<6} "
                    f"{'OK ' if ok else 'BAD'} e2e={record['speech_end_to_first_audio_ms']}ms "
                    f"stt={record['stages_ms'].get('stt')}ms tts={record['stages_ms'].get('tts_first_byte')}ms "
                    f"'{meta.get('transcript', '')}'"
                )
    total = runs * len(cases)
    summary = {
        "label": label,
        "runs": runs,
        "utterances": total,
        "wake_detected": detected,
        "intent_correct": correct,
        "engines": {"wake": wake.name, "vad": vad.name, "stt": stt.name, "tts": tts.name},
        "stt_load_and_warm_up_s": round(load_s, 2),
        "stt_prompt": settings.stt_prompt,
        "language_correct": language_ok,
        "environment": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "system": f"{platform.system()} {platform.release()}",
        },
        "stages": [s.as_row() for s in summarize(records)],
    }
    (out_dir / f"summary-{label}.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(markdown(summary))
    return summary


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"### {summary['label']}: {summary['engines']['stt']}, {summary['engines']['tts']}",
        "",
        f"wake detected {summary['wake_detected']}/{summary['utterances']}, "
        f"intent correct {summary['intent_correct']}/{summary['utterances']}",
        "",
        "| stage | n | median ms | p95 ms | min ms | max ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary["stages"]:
        lines.append(
            f"| {row['stage']} | {row['n']} | {row['median_ms']} | {row['p95_ms']} "
            f"| {row['min_ms']} | {row['max_ms']} |"
        )
    return "\n".join(lines)


def main(fixtures: Path, out_dir: Path, runs: int, label: str) -> None:
    asyncio.run(run(fixtures, out_dir, runs, Settings.from_env(), label))


STT_MATRIX: tuple[tuple[str, str | None, bool, int], ...] = (
    # model, fixed language (None = parallel en+tr), vocabulary prompt, CPU threads per decode
    ("tiny", None, True, 4),
    ("base", None, True, 4),
    ("base", None, False, 4),
    ("base", None, True, 8),
    ("base", "fixed", True, 8),
    ("small", None, True, 4),
)


async def run_stt(fixtures: Path, out_dir: Path, runs: int, models: list[str]) -> list[dict[str, Any]]:
    """STT alone on the exact command span of each fixture: latency and accuracy per configuration.

    Feeds each command (with 300 ms of padding on both sides, like an endpointed utterance) straight to
    :class:`FasterWhisperStt`, so the numbers isolate the model/threads/language-strategy choice of ADR 0002.
    "fixed" decodes only in the fixture's true language: the monolingual upper bound.
    """
    import numpy as np

    from gideon.app import build_router
    from gideon.audio.sources import read_wav
    from gideon.engines.stt_faster_whisper import FasterWhisperStt

    cases = load_manifest(fixtures)
    settings = Settings()
    router = build_router(settings, notes_path=out_dir / "stt-notes.jsonl")
    clips = []
    for case in cases:
        pcm = read_wav(fixtures / case["file"])
        start = max(0, int((case["command_start_s"] - 0.3) * 16_000))
        end = min(len(pcm), int((case["command_end_s"] + 0.3) * 16_000))
        clips.append((case, pcm[start:end]))

    rows: list[dict[str, Any]] = []
    for model, fixed, prompt, threads in STT_MATRIX:
        if model not in models:
            continue
        engines: dict[str, FasterWhisperStt] = {}
        langs = ("en", "tr") if fixed else (None,)
        for lang in langs:
            engines[str(lang)] = FasterWhisperStt(
                model,
                cpu_threads=threads,
                language=lang,
                prompts={lg: router.vocabulary(lg) for lg in ("en", "tr")} if prompt else None,
            )
            engines[str(lang)].warm_up()
        latencies: list[float] = []
        intents = languages = 0
        for _ in range(runs):
            for case, clip in clips:
                stt = engines[case["language"] if fixed else "None"]
                t0 = time.perf_counter()
                result = await stt.transcribe(clip)
                latencies.append((time.perf_counter() - t0) * 1000)
                match = router.match(result.text, result.language)
                intents += bool(match and (match.skill, match.intent) == (case["skill"], case["intent"]))
                languages += result.language == case["language"]
        n = len(latencies)
        row = {
            "model": model,
            "languages": "fixed (oracle)" if fixed else "en+tr parallel",
            "prompt": prompt,
            "threads": f"{threads}x{1 if fixed else 2}",
            "n": n,
            "median_ms": round(float(np.median(latencies)), 1),
            "p95_ms": round(percentile(latencies, 95), 1),
            "intent_correct": f"{intents}/{n}",
            "language_correct": f"{languages}/{n}",
        }
        rows.append(row)
        print(row, flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"stt-matrix-{'-'.join(models)}.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )
    return rows
