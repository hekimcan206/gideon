"""Synthetic test utterances, generated at build time with edge-tts (they are not committed).

Microsoft's terms for redistributing Edge "Read aloud" audio are unclear, so the repository ships the *script*
(this file) and the manifest, never the audio. Each WAV is: 0.6 s silence, "Hey Jarvis", 0.35 s pause, a command,
1.2 s silence; 16 kHz mono 16-bit, the pipeline's capture format. Speaker voices are deliberately different from
the voices GIDEON answers with.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from gideon.audio.sources import silence, write_wav


@dataclass(frozen=True)
class FixtureCase:
    name: str
    voice: str
    command: str
    language: str
    skill: str
    intent: str
    wake: str = "Hey Jarvis!"


CASES: tuple[FixtureCase, ...] = (
    FixtureCase("en_time", "en-US-GuyNeural", "What time is it?", "en", "clock", "time.now"),
    FixtureCase("en_date", "en-US-JennyNeural", "What's the date today?", "en", "clock", "date.today"),
    FixtureCase("en_timer", "en-US-GuyNeural", "Set a timer for five minutes.", "en", "timer", "timer.set"),
    FixtureCase("en_note", "en-US-JennyNeural", "Take a note: buy milk.", "en", "notes", "note.add"),
    FixtureCase("en_open", "en-US-GuyNeural", "Open the calculator.", "en", "apps", "app.open"),
    FixtureCase("tr_time", "tr-TR-AhmetNeural", "Saat kaç?", "tr", "clock", "time.now"),
    FixtureCase("tr_system", "tr-TR-EmelNeural", "Sistem durumu nedir?", "tr", "system", "system.info"),
    FixtureCase(
        "tr_timer", "tr-TR-AhmetNeural", "Beş dakikalık zamanlayıcı kur.", "tr", "timer", "timer.set"
    ),
    FixtureCase("tr_notes", "tr-TR-EmelNeural", "Notlarımı oku.", "tr", "notes", "note.list"),
)


async def _synthesize(text: str, voice: str) -> np.ndarray:
    import edge_tts
    import miniaudio

    mp3 = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk.get("type") == "audio":
            mp3.extend(chunk["data"])
    decoded = miniaudio.decode(
        bytes(mp3), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=16_000
    )
    return np.frombuffer(decoded.samples, dtype=np.int16)


async def generate(out_dir: Path, cases: tuple[FixtureCase, ...] = CASES) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case in cases:
        wake = await _synthesize(case.wake, case.voice)
        command = await _synthesize(case.command, case.voice)
        pcm = np.concatenate([silence(0.6), wake, silence(0.35), command, silence(1.2)])
        write_wav(out_dir / f"{case.name}.wav", pcm)
        command_start = round(0.6 + len(wake) / 16_000 + 0.35, 3)
        manifest.append(
            {
                **asdict(case),
                "file": f"{case.name}.wav",
                "seconds": round(len(pcm) / 16_000, 2),
                "command_start_s": command_start,
                "command_end_s": round(command_start + len(command) / 16_000, 3),
            }
        )
    path = out_dir / "manifest.json"
    path.write_text(json.dumps({"source": "edge-tts (generated at build time)", "cases": manifest},
                               indent=2, ensure_ascii=False) + "\n", encoding="utf-8")  # fmt: skip
    return path


def load_manifest(fixtures_dir: Path) -> list[dict[str, Any]]:
    data = json.loads((fixtures_dir / "manifest.json").read_text(encoding="utf-8"))
    return list(data["cases"])


def main(out_dir: Path) -> None:
    path = asyncio.run(generate(out_dir))
    print(f"wrote {len(CASES)} fixtures and {path}")
