# ADR 0001 — Split the assistant into a Python backend and an Electron UI connected by a versioned WebSocket protocol

- Status: accepted
- Date: 2026-11-08

## Context

GIDEON needs two very different runtimes:

- **Speech.** openWakeWord, WebRTC VAD, faster-whisper (CTranslate2) and edge-tts are Python libraries. Wake-word
  scoring runs every 80 ms, and STT keeps several cores busy for half a second per utterance. The audio loop must
  never wait for the UI.
- **Desktop UI.** A window with live state, a transcript, a latency waterfall, hotkeys and a tray-style lifecycle.
  The web platform is the fastest way to build that, and Electron is the usual way to ship it on Windows, macOS and
  Linux.

The question is how the two talk to each other, and what is allowed to cross the boundary.

## Options considered

1. **All in Python** (PySide6/Qt or Tkinter UI). One process and no IPC. But a Qt UI in the same interpreter shares
   the GIL with the audio loop: a slow repaint delays wake-word frames. The UI stack is also further from the web
   skills this project is meant to show.
2. **All in Node/Electron** (whisper.cpp bindings, ONNX Runtime for Node, a JavaScript TTS client). One language. But
   openWakeWord's feature pipeline and faster-whisper's VAD and decoding options are Python-first. Rebuilding them
   is a project of its own, and native Node addons make packaging fragile.
3. **Python child process over stdio (JSON lines).** No port, and the lifetime is tied to Electron. But only one
   client can attach, so there is no browser preview, no second window and no test client. Framing, backpressure
   and reconnects all have to be built by hand.
4. **Python backend + Electron UI over a loopback WebSocket, with a typed, versioned message protocol.** Any number
   of clients: the Electron window, a browser in the compose demo, Playwright, pytest. The `websockets` library
   handles framing, ping/pong and limits. The UI can reconnect while the backend restarts.
5. **gRPC / HTTP + SSE.** gRPC-Web from a renderer needs a proxy. SSE is one-way, and commands would need a second
   channel.

## Decision

Option 4.

- **Process split.** The Python backend owns audio capture, every engine and the skills. The Electron renderer is
  only a client. In production the Electron main process starts the backend (`GIDEON_SPAWN_BACKEND=1`) and stops it
  on quit. During development and in Docker the backend runs on its own.
- **Protocol.** Pydantic models in `backend/src/gideon/protocol.py` are the single source of truth. Every frame
  has `v` (protocol version, now `1`) and a `type` discriminator. Server→client messages are `hello`, `state`,
  `wake`, `transcript`, `response`, `trace`, `notification`, `error` and `pong`. Client→server messages are `hello`,
  `ptt`, `cancel`, `say` and `ping`. `extra="forbid"` turns a typo into an error instead of a silently ignored field.
- **Shared types.** `python -m gideon export-schema` writes `protocol/schema.json`, and `npm run gen:protocol`
  generates `ui/src/shared/protocol.ts` with json-schema-to-typescript. CI regenerates both and fails on any diff, so
  a renamed field breaks the UI build instead of showing up as `undefined` at runtime. Fields with non-null
  defaults (`v`, `type`, `barge_in`) are marked required in the exported schema. Without that, the generated union
  could not narrow on `type`.
- **Compatibility rule.** Adding an optional field keeps `v`. Removing, renaming or redefining a field bumps it. A
  peer that sees another version closes the connection with code 4426, and the UI says so instead of misreading
  frames.
- **Ordering.** Pipeline events are emitted synchronously, in the order the pipeline produced them. An earlier
  version scheduled `state` messages as tasks, so a `trace` could overtake the `idle` that came before it. A server
  test caught it.
- **Connection management in the UI.** A `hello` handshake carries the token. A heartbeat ping every 10 s drops
  half-open sockets. Reconnects use exponential back-off with full jitter (250 ms base, 8 s cap). The attempt
  counter resets only when the server's `hello` arrives, not on TCP open, so a backend that crashes during start-up
  is not hammered.

## Consequences

- Positive: the audio loop and the UI fail independently. The renderer has no Node.js access at all (ADR scope:
  see [threat model](../threat-model.md)). Every behaviour is testable from pytest with a real socket
  (`tests/test_server.py`), and the same renderer bundle runs in a browser for the compose demo and Playwright.
- Negative: a loopback port is an attack surface that stdio would not have. It is mitigated by binding to
  `127.0.0.1`, an Origin allow-list, a per-launch random token and frame limits (threat model T1–T3). There are
  two toolchains (pip and npm) and two test runners.
- Follow-up: if the protocol grows (audio streaming to the renderer, settings), add an optional `capabilities` list
  to `hello` rather than bumping `v` for every additive change.
