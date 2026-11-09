# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). It is written by hand when a release is cut.

## [1.0.0] - 2026-11-09

### Features

- Python 3.12 asyncio voice pipeline with explicit, swappable stages: capture → openWakeWord ("hey jarvis") →
  WebRTC VAD end-of-utterance → faster-whisper (CPU, int8) → intent router and skills → edge-tts streaming →
  playback, plus barge-in (a wake word or Stop cancels a running answer).
- Bilingual (English/Turkish) speech-to-text: parallel per-language decoding, where the highest mean log-probability
  wins, primed with the skills' example phrases.
- Skills: time and date, timers with spoken announcements, system info, allow-listed app launcher (no shell),
  notes, help; optional LLM fallback behind a provider interface (Ollama, off by default).
- Versioned, typed WebSocket protocol (pydantic → JSON Schema → generated TypeScript types), with an Origin
  allow-list, a per-launch token and frame limits.
- Per-stage latency instrumentation on monotonic timestamps, written to a JSONL trace, summarised as median/p95 by
  `python -m gideon bench` and `bench-stt`.
- Electron + Vite + TypeScript UI with sandboxed renderer, context isolation, preload bridge and strict CSP: live
  state, transcript, latency waterfall, reconnect with jittered exponential back-off.
- Simulated-audio mode: synthetic WAV utterances generated with edge-tts and replayed in real time;
  `docker compose up` runs the full demo without a microphone.
- Install scripts for Windows (PowerShell) and Linux/macOS (bash).
