"""The real pipeline on the generated WAV fixtures: openWakeWord + WebRTC VAD + faster-whisper (CPU, int8).

Skipped unless the engines are installed and ``python -m gideon fixtures`` has been run. TTS is the offline
simulated engine here so the test does not depend on Microsoft's service; edge-tts streaming is covered by
``test_edge_tts_streams_audio`` (needs internet, opt-in with GIDEON_NETWORK_TESTS=1) and by the benchmark.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.engines

FIXTURES = Path(os.environ.get("GIDEON_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures"))
STT_MODEL = os.environ.get("GIDEON_TEST_STT_MODEL", "base")

pytest.importorskip("faster_whisper")
pytest.importorskip("openwakeword")
pytest.importorskip("webrtcvad")
if not (FIXTURES / "manifest.json").exists():
    pytest.skip(f"no fixtures in {FIXTURES} (run `python -m gideon fixtures`)", allow_module_level=True)

from gideon.audio.simulated import SimulatedTts  # noqa: E402
from gideon.bench import run_case  # noqa: E402
from gideon.config import Settings  # noqa: E402
from gideon.engines.vad_webrtc import WebRtcVad  # noqa: E402
from gideon.engines.wake_openwakeword import OpenWakeWordDetector  # noqa: E402
from gideon.fixtures import load_manifest  # noqa: E402
from gideon.latency import TraceLog  # noqa: E402


@pytest.fixture(scope="module")
def engines() -> dict[str, Any]:
    from gideon.app import build_router, build_stt

    settings = Settings(stt_model=STT_MODEL)
    stt = build_stt(settings, build_router(settings))
    return {
        "wake": OpenWakeWordDetector("hey_jarvis"),
        "vad": WebRtcVad(2),
        "stt": stt,
        "tts": SimulatedTts(),
    }


@pytest.fixture(scope="module")
def results(engines: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("integration")
    settings = Settings(data_dir=tmp, apps_dry_run=True)
    out: dict[str, dict[str, Any]] = {}
    import asyncio

    async def go() -> None:
        for case in load_manifest(FIXTURES):
            record = await run_case(
                FIXTURES / case["file"], settings=settings, notes_path=tmp / "notes.jsonl",
                trace_log=TraceLog(tmp / "traces.jsonl"), **engines,
            )  # fmt: skip
            out[case["name"]] = {"case": case, "record": record}

    asyncio.run(go())
    return out


def test_wake_word_fires_on_every_fixture(results: dict[str, dict[str, Any]]) -> None:
    missed = [name for name, r in results.items() if r["record"] is None]
    assert missed == []


def test_every_fixture_reaches_the_expected_skill(results: dict[str, dict[str, Any]]) -> None:
    wrong = {}
    for name, r in results.items():
        meta = (r["record"] or {}).get("meta", {})
        if (meta.get("skill"), meta.get("intent")) != (r["case"]["skill"], r["case"]["intent"]):
            wrong[name] = meta.get("transcript")
    assert wrong == {}


def test_traces_are_complete_and_ordered(results: dict[str, dict[str, Any]]) -> None:
    for r in results.values():
        record = r["record"]
        assert record["outcome"] == "ok"
        assert "trace_order_error" not in record["meta"]
        stages = record["stages_ms"]
        for stage in (
            "wake_detect",
            "utterance",
            "endpoint",
            "stt",
            "route",
            "tts_first_byte",
            "playback_start",
        ):
            assert stage in stages
        # The endpoint hangover is a configured policy: 560 ms of silence, give or take one 80 ms frame.
        assert 480 <= stages["endpoint"] <= 800


@pytest.mark.skipif(os.environ.get("GIDEON_NETWORK_TESTS") != "1", reason="needs internet (edge-tts)")
async def test_edge_tts_streams_audio_in_chunks() -> None:
    from gideon.engines.tts_edge import EdgeTts

    chunks = [c async for c in EdgeTts().stream("Saat on dört sıfır beş.", "tr")]
    assert len(chunks) > 1
    assert b"".join(chunks)[:3] in (b"ID3", b"\xff\xf3", b"\xff\xfb") or b"".join(chunks)[0] == 0xFF


def test_parallel_bilingual_decoding_picks_the_spoken_language(results: dict[str, dict[str, Any]]) -> None:
    wrong = {
        name: r["record"]["meta"].get("stt_language")
        for name, r in results.items()
        if r["record"]["meta"].get("stt_language") != r["case"]["language"]
    }
    assert wrong == {}
