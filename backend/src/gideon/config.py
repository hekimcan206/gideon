"""Settings from environment variables (12-factor; the Electron main process and docker compose both set them)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


def _data_dir() -> Path:
    base = (
        os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    )
    return Path(base) / "gideon"


DEFAULT_ORIGINS = (
    "app://gideon",  # packaged Electron renderer (custom protocol)
    "http://localhost:5173",  # Vite dev server
    "http://127.0.0.1:5173",
    "http://localhost:18766",  # browser-mode UI from docker compose
    "http://127.0.0.1:18766",
)


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 18765
    token: str | None = None
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS

    audio: str = "mic"  # mic | wav
    wav_dir: Path = Path("fixtures")
    wav_loop: bool = True
    wav_gap_s: float = 3.0

    wake_model: str = "hey_jarvis"
    wake_threshold: float = 0.5
    vad: str = "webrtc"  # webrtc | energy
    vad_aggressiveness: int = 2
    endpoint_silence_ms: int = 560

    stt_model: str = "base"
    stt_compute_type: str = "int8"
    stt_threads: int | None = None
    stt_language: str | None = None  # None = decode en and tr in parallel, keep the more confident
    stt_prompt: bool = True  # prime the decoder with the skills' example phrases

    tts: str = "edge"  # edge | simulated
    voice_en: str = "en-GB-RyanNeural"
    voice_tr: str = "tr-TR-AhmetNeural"

    data_dir: Path = field(default_factory=_data_dir)
    trace_path: Path | None = None
    apps_file: Path | None = None
    apps_dry_run: bool = False
    llm: str = "off"  # off | ollama
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(_env("GIDEON_DATA_DIR", str(_data_dir())))
        origins = tuple(
            o.strip() for o in _env("GIDEON_ALLOWED_ORIGINS", ",".join(DEFAULT_ORIGINS)).split(",") if o
        )
        threads = _env("GIDEON_STT_THREADS", "")
        trace = _env("GIDEON_TRACE_PATH", str(data_dir / "traces.jsonl"))
        apps = _env("GIDEON_APPS_FILE", "")
        return cls(
            host=_env("GIDEON_HOST", "127.0.0.1"),
            port=int(_env("GIDEON_PORT", "18765")),
            token=_env("GIDEON_TOKEN", "") or None,
            allowed_origins=origins,
            audio=_env("GIDEON_AUDIO", "mic"),
            wav_dir=Path(_env("GIDEON_WAV_DIR", "fixtures")),
            wav_loop=_bool("GIDEON_WAV_LOOP", True),
            wav_gap_s=float(_env("GIDEON_WAV_GAP_S", "3.0")),
            wake_model=_env("GIDEON_WAKE_MODEL", "hey_jarvis"),
            wake_threshold=float(_env("GIDEON_WAKE_THRESHOLD", "0.5")),
            vad=_env("GIDEON_VAD", "webrtc"),
            vad_aggressiveness=int(_env("GIDEON_VAD_AGGRESSIVENESS", "2")),
            endpoint_silence_ms=int(_env("GIDEON_ENDPOINT_SILENCE_MS", "560")),
            stt_model=_env("GIDEON_STT_MODEL", "base"),
            stt_compute_type=_env("GIDEON_STT_COMPUTE_TYPE", "int8"),
            stt_threads=int(threads) if threads else None,
            stt_language=_env("GIDEON_STT_LANGUAGE", "") or None,
            stt_prompt=_bool("GIDEON_STT_PROMPT", True),
            tts=_env("GIDEON_TTS", "edge"),
            voice_en=_env("GIDEON_VOICE_EN", "en-GB-RyanNeural"),
            voice_tr=_env("GIDEON_VOICE_TR", "tr-TR-AhmetNeural"),
            data_dir=data_dir,
            trace_path=Path(trace) if trace else None,
            apps_file=Path(apps) if apps else None,
            apps_dry_run=_bool("GIDEON_APPS_DRY_RUN", False),
            llm=_env("GIDEON_LLM", "off"),
            ollama_url=_env("GIDEON_OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=_env("GIDEON_OLLAMA_MODEL", "llama3.2:3b"),
            log_level=_env("GIDEON_LOG_LEVEL", "INFO"),
        )
