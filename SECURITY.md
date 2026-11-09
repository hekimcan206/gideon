# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting
(**Security → Report a vulnerability**). Expect an acknowledgement within 5 working days, and a fix or mitigation
plan within 30 days of confirmation.

Please do not attach recordings of real people's voices to reports. The synthetic fixtures
(`python -m gideon fixtures`) are enough to reproduce pipeline problems.

## Security-relevant defaults

| Area | Default |
|---|---|
| Control socket | binds to `127.0.0.1:18765`; browser `Origin` allow-list checked before the WebSocket upgrade (403); per-launch random token when Electron starts the backend; 64 KiB frame limit; schema-validated frames (`extra="forbid"`) |
| Actions | `open app` runs allow-listed argv only, via `create_subprocess_exec` (no shell); the transcript never becomes part of a command line; dry-run in Docker |
| Renderer | `contextIsolation`, `sandbox`, no `nodeIntegration`, no `webview`; preload exposes one frozen config object; navigation and new windows blocked; every permission request denied; strict CSP (`script-src 'self'`, no inline) as a header and a meta tag; untrusted text rendered with `textContent` |
| Privacy | audio stays in memory in the backend process; speech-to-text is local; only answer text goes to edge-tts; the LLM fallback is off by default (local Ollama when enabled) |
| Container | non-root UID 10001, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, loopback-only ports, models baked in with `HF_HUB_OFFLINE=1` |
| Supply chain | pinned Python and npm versions, npm lock file, `pip-audit` and `npm audit` in CI, CodeQL `security-and-quality` for Python, TypeScript and Actions |

Details and residual risks: [docs/threat-model.md](docs/threat-model.md).
