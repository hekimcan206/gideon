# ADR 0004 — End-of-utterance: WebRTC VAD per 80 ms frame with a 560 ms silence hangover

- Status: accepted
- Date: 2026-11-08

## Context

After the wake word, GIDEON has to decide when the user has finished the command. Every millisecond of waiting is
added to the response time, and the benchmark shows that this wait is a fixed, configured part of the
"end of speech → first audio" number. Ending too early cuts people off mid-command, especially in "set a timer
for … five minutes", where speakers pause before the number. Constraints:

- Decisions are made on the pipeline's 80 ms frames (openWakeWord's hop), with timestamps comparable to every other
  stage.
- It must cost almost nothing per frame, because it runs inside the capture loop.
- The policy (how long, how much speech counts) must be testable without audio.

## Options considered

1. **Fixed recording window** (e.g. 4 s after the wake word). Trivial, but it adds seconds to short commands and cuts
   long ones.
2. **Energy threshold.** No dependency, but it fails with fans, music and microphone gain differences. It is kept
   only as the fallback and the unit-test VAD (`EnergyVad`).
3. **Silero VAD** (ONNX). More accurate in noise, and faster-whisper bundles it. It costs about 1 ms per 30 ms chunk
   on CPU, which is still small. It is already used *inside* STT (`vad_filter=True`) to trim silence before
   decoding.
4. **WebRTC VAD** (GMM) per 20 ms sub-frame, aggregated per 80 ms frame (chosen for endpointing). Microseconds per
   call, robust enough for a near-field desktop microphone, and aggressiveness 0–3 is configurable.
5. **Semantic endpointing** (a model that predicts "the sentence is complete"). This is where production
   assistants are going. It needs streaming partial transcripts, which conflicts with ADR 0002's single decode.

## Decision

Option 4, with the policy separated from the detector (`gideon/audio/endpoint.py`):

- A frame is speech if ≥ 2 of its four 20 ms sub-frames are voiced (aggressiveness 2).
- Speech *starts* after 160 ms of consecutive voiced frames. Isolated clicks and breaths reset the counter.
- Speech *ends* after **560 ms** of silence (7 frames). `speech_end` is stamped with the capture time of the last
  voiced frame, so the trace shows the hangover as its own `endpoint` stage instead of hiding it in STT.
- Safety limits: no speech within 4 s after the wake word → back to idle (`no_speech`). An utterance longer than 8 s
  is cut (`max_length`).
- Everything is configurable (`GIDEON_ENDPOINT_SILENCE_MS`, `GIDEON_VAD_AGGRESSIVENESS`).

## Consequences

- The measured `endpoint` stage is 562–565 ms on every fixture ([benchmarks](../benchmarks.md)): the configured
  560 ms plus the time to the next frame boundary, so it is predictable. It is about a quarter of "end of speech →
  first audio" on this machine, and it is the cheapest lever: 400 ms saves 160 ms on every interaction, at the cost
  of more cut-offs for hesitant speakers.
- The endpointer is unit-tested frame by frame with scripted audio (`test_state_and_endpoint.py`). The integration
  test asserts the 480–800 ms band on real audio.
- Next step: adaptive hangover. Use a shorter wait when the partial transcript already matches a complete intent
  ("what time is it"), and a longer one after "set a timer for". That needs streaming partials and is listed under
  limitations.
