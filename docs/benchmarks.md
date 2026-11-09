# Benchmarks

All numbers on this page were measured on the machine described below, on 2026-11-09 and 2026-11-10. Nothing is estimated. Raw
data: `bench-results/` (not committed; regenerate with the commands at the end).

## Environment

| | |
|---|---|
| CPU | Intel Core i7-13620H (6 P-cores + 4 E-cores, 16 threads), laptop |
| RAM | 16 GB (Docker Desktop VM: 7.6 GB, 16 vCPUs) |
| OS | Windows 11 Home; backend in Docker Desktop 29.9 (WSL 2), image `python:3.12-slim` |
| Python / engines | CPython 3.12, faster-whisper 1.2.1 (CTranslate2 4.8.5, int8), openWakeWord 0.6.0 (ONNX Runtime 1.31.1), webrtcvad-wheels 2.0.14.post1, edge-tts 7.3.1 |
| Network | home connection; edge-tts goes to Microsoft's online service. DNS failed intermittently during the measurements (see Results) |
| Host load | **not idle**: about 9 other containers from unrelated projects were running (one .NET API with Postgres/Redis using about 1 core), and the host's own CPU load was 12–48% between samples. Medians are reported for that reason; p95 shows the interference. |

## Method

- **Input**: 9 synthetic utterances (5 English, 4 Turkish), each "Hey Jarvis!" + 0.35 s + a command, spoken by four
  edge-tts voices that differ from GIDEON's answer voices (`python -m gideon fixtures`; 16 kHz mono).
- **Real time**: `WavSource` releases each 80 ms frame no earlier than it would come out of a microphone, and stamps it
  with `time.monotonic_ns()` at release. Wake-word scoring, the endpoint hangover and every later stage therefore see
  audio exactly on the capture schedule.
- **Engines**: the real ones. openWakeWord `hey_jarvis` (threshold 0.5), WebRTC VAD (aggressiveness 2, 560 ms
  hangover), faster-whisper `base` int8 with en ∥ tr decoding (4 threads × 2 workers) and vocabulary prompts,
  regex router, **edge-tts streaming over the internet**. The sink is the simulated speaker, so `playback_start` is
  the hand-off of the first chunk with no audio device latency.
- **Rounds**: 10 rounds over the 9 fixtures = 90 utterances, one fresh `Assistant` per utterance. The engines are
  shared and warm.
- **Statistics**: median and nearest-rank p95 (always an observed value) over interactions with outcome `ok`.

### Stage definitions

| stage | from → to | what it is |
|---|---|---|
| wake_detect | capture of the frame that crossed the threshold → detector returned | openWakeWord processing on that frame |
| utterance | wake → capture of the last voiced frame | the user talking (not system latency) |
| endpoint | last voiced frame → end-of-utterance decision | the configured 560 ms silence hangover |
| stt | endpoint → transcript | faster-whisper, both languages |
| route | transcript → answer text | intent match + skill |
| tts_first_byte | answer text → first MP3 chunk | edge-tts round trip and synthesis start |
| playback_start | first chunk → handed to the output | sink hand-off |
| **speech_end → first audio** | last voiced frame → first audio byte | what the user waits for |
| **wake → first audio** | wake detection → first audio byte | includes the time spent speaking the command |

## Results: end to end (`python -m gideon bench --runs 10`)

90 utterances. Wake word detected 90/90, intent correct 90/90, language correct 90/90. 83 interactions finished
with outcome `ok`. The other 7 (all in round 9) failed with `ClientConnectorDNSError`, because edge-tts could not
resolve `speech.platform.bing.com` while the home network's DNS was down. Wake word, STT and routing had already
completed for those 7. They are excluded from the latency statistics, not retried.

| stage | n | median ms | p95 ms | min ms | max ms |
|---|---:|---:|---:|---:|---:|
| wake_detect | 83 | 2.5 | 4.9 | 1.8 | 5.7 |
| utterance | 83 | 2558.3 | 3597.7 | 2235.1 | 3598.3 |
| endpoint | 83 | 562.8 | 565.2 | 561.0 | 566.0 |
| stt | 83 | 1085.7 | 1876.4 | 553.1 | 2486.1 |
| route | 83 | 0.2 | 0.5 | 0.1 | 1.5 |
| tts_first_byte | 83 | 1159.5 | 2580.2 | 993.5 | 4288.0 |
| playback_start | 83 | 0.0 | 0.0 | 0.0 | 0.0 |
| **speech_end → first audio** | 83 | **3038.8** | **4652.7** | 2131.9 | 5480.4 |
| **wake → first audio** | 83 | **5690.5** | **7210.0** | 4782.7 | 7957.2 |

Reading it: the user waits about 3 s after they stop talking. That time splits into roughly equal parts: the
endpoint hangover (a fixed 0.56 s), Whisper (about 1.1 s) and the first edge-tts byte over the internet (about
1.2 s). Routing and playback hand-off cost nothing by comparison. Most of the p95 spread comes from edge-tts and
from STT competing with the other containers for CPU.

Two more attempts at a clean run were made after the network came back. DNS failed again in both (25/90 and 85/90
TTS failures), so they are kept only as raw logs (`bench-results/*-dnsoutage2*`, `*-dnsoutage3*`) and not reported.

## Results: speech-to-text matrix (`python -m gideon bench-stt`)

The 9 fixtures × 5 rounds = 45 transcriptions per row, STT stage only (endpointed audio → text).

| model | languages | vocabulary prompt | threads × workers | median ms | p95 ms | intent correct | language correct |
|---|---|---|---|---:|---:|---:|---:|
| tiny | en ∥ tr | yes | 4 × 2 | 618.9 | 1289.2 | 40/45 | 45/45 |
| **base** | **en ∥ tr** | **yes** | **4 × 2** | **931.8** | **1988.0** | **45/45** | **45/45** |
| base | en ∥ tr | no | 4 × 2 | 1398.5 | 2115.7 | 40/45 | 45/45 |
| base | en ∥ tr | yes | 8 × 2 (oversubscribed) | 1233.6 | 3935.4 | 45/45 | 45/45 |
| base | fixed (oracle) | yes | 8 × 1 | 547.0 | 689.4 | 45/45 | 45/45 |
| small | en ∥ tr | yes | 4 × 2 | 1848.2 | 5846.8 | 45/45 | 45/45 |

- `base` with the prompt is the default: it is the cheapest configuration that gets every intent right.
- Decoding both languages costs about 400 ms over an oracle that already knows the language. Whisper's own language
  detection is not an option for short commands (see the README).
- 8 threads per decode × 2 decodes oversubscribes the 6 performance cores. p95 doubles, which is why the default
  splits the physical cores (`perf(stt)`).
- The `small` row comes from a separate run (`--models small`, `stt-matrix-small.json`). It is twice as slow as
  `base` and gains no accuracy on these fixtures.
- Caveat: the vocabulary prompt contains phrasing similar to 7 of the 9 fixtures, so the prompt's effect is
  probably overstated here.

## Results: endpoint start-grace fix

Same benchmark, before and after `fix(endpoint)` (the first 240 ms after the wake word cannot start an utterance):

| | before | after |
|---|---:|---:|
| empty transcript (utterance was only the wake-word tail) | 5/90 | 0/90 |
| intent correct | 85/90 | 90/90 |
| other failures (edge-tts DNS / chunk timeout) | 8/90 | 7/90 |

## Reproduce

```bash
python -m gideon fixtures                    # synthesise the 9 WAV fixtures (needs network for edge-tts)
python -m gideon bench --runs 10             # end to end; writes bench-results/traces-*.jsonl and summary-*.json
python -m gideon bench-stt                   # STT matrix (tiny, base)
python -m gideon bench-stt --models small    # the small row
```

Inside Docker on Windows: `bash scripts/dev-docker.sh python -m gideon bench --runs 10`.
