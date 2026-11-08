from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np
import pytest

from gideon.audio.interfaces import FRAME_SAMPLES
from gideon.audio.simulated import SimulatedTts
from gideon.audio.sources import WavSource, read_wav, silence, to_frames, write_wav


def test_frames_are_zero_padded_to_80_ms() -> None:
    frames = to_frames(np.ones(FRAME_SAMPLES * 2 + 5, dtype=np.int16))
    assert [len(f) for f in frames] == [FRAME_SAMPLES] * 3
    assert frames[-1][5:].sum() == 0


def test_wav_round_trip_and_format_check(tmp_path: Path) -> None:
    pcm = (np.arange(1600) % 100).astype(np.int16)
    write_wav(tmp_path / "a.wav", pcm)
    assert np.array_equal(read_wav(tmp_path / "a.wav"), pcm)

    with wave.open(str(tmp_path / "stereo.wav"), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44_100)
        wav.writeframes(b"\x00" * 400)
    with pytest.raises(ValueError, match="16 kHz mono"):
        read_wav(tmp_path / "stereo.wav")


async def test_realtime_source_never_releases_audio_early(tmp_path: Path) -> None:
    write_wav(tmp_path / "half.wav", silence(0.4))  # 5 frames
    source = WavSource([tmp_path / "half.wav"], gap_s=0.0, tail_s=0.0)
    start = time.monotonic_ns()
    frames = [f async for f in source.frames()]
    assert len(frames) == 5
    for frame in frames:
        assert frame.captured_at_ns - start >= (frame.index + 1) * 80_000_000 - 2_000_000
    assert [f.index for f in frames] == list(range(5))


async def test_fast_source_adds_gaps_and_tail(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", silence(0.16))
    write_wav(tmp_path / "b.wav", silence(0.16))
    source = WavSource([tmp_path / "a.wav", tmp_path / "b.wav"], realtime=False, gap_s=0.16, tail_s=0.32)
    frames = [f async for f in source.frames()]
    assert len(frames) == 2 * (2 + 2) + 4


def test_source_needs_files() -> None:
    with pytest.raises(ValueError, match="at least one"):
        WavSource([])


async def test_simulated_tts_yields_bytes_proportional_to_text() -> None:
    tts = SimulatedTts(chars_per_second=10)
    short = b"".join([c async for c in tts.stream("hi", "en")])
    long = b"".join([c async for c in tts.stream("x" * 100, "en")])
    assert len(long) == 60_000  # 10 s of speech at 6 000 B/s
    assert 0 < len(short) < len(long)
