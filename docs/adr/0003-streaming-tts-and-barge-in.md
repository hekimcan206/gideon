# ADR 0003 — Stream TTS audio as it arrives, and let the wake word interrupt an answer (barge-in)

- Status: accepted
- Date: 2026-11-08

## Context

After the skill has produced its answer text, two things decide how responsive GIDEON feels:

1. **Time to first audio.** edge-tts sends the synthesised MP3 over a WebSocket in chunks of about 1.4 KB, and the
   first chunk arrives long before the last one. If playback waits for the whole file, synthesis time and download
   time are both added to every answer.
2. **Interruptibility.** Answers can be long ("your latest notes: ..."). A user who says "Hey Jarvis" again while
   GIDEON is speaking expects it to stop and listen, not to finish the sentence and then ignore the new request.

## Options considered

### Synthesis and playback

1. **Synthesise to a file, then play it.** Simple, and the file can be cached. First audio = full synthesis +
   download.
2. **Stream chunks to the sink as they arrive** (chosen). First audio = time to the first chunk. The sink must
   decode a stream, not a file.
3. **Local TTS (Piper/Coqui).** Offline and private, with no network jitter. Turkish voice quality varies more, and
   it adds a model to manage. It stays possible behind the `TextToSpeech` interface (see Consequences).

### Interruption

1. **No barge-in.** Capture keeps running, but wake words are ignored while speaking.
2. **Barge-in on the wake word** (chosen). The capture loop keeps feeding openWakeWord in every state. A detection
   while *thinking* or *speaking* cancels the response task and starts a new interaction in *listening*.
3. **Barge-in on any speech (VAD).** More natural, but without acoustic echo cancellation the assistant's own voice
   coming out of the speakers triggers the VAD. That needs AEC (WebRTC APM or the OS's communication mode), which
   is out of scope for v1.

## Decision

- `TextToSpeech.stream(text, language)` is an async iterator of encoded chunks. The pipeline wraps it so the
  **first non-empty chunk stamps `tts_first_byte`**. The sink calls `on_started()` when it hands the first audio to
  the output, which stamps `playback_start` and moves the state machine to *speaking*.
- `SpeakerSink` (live mode) feeds chunks into a blocking byte source that miniaudio's MP3 decoder pulls from on its
  own thread. Playback starts after the first chunk while the rest is still downloading. `SimulatedSpeakerSink`
  (WAV mode, CI, compose) starts "playback" at the first chunk and then waits for as long as the audio would play
  (bytes ÷ 6 000 B/s, edge-tts' 48 kbit/s), so the *speaking* phase and barge-in can be exercised without a sound
  card.
- **Barge-in is task cancellation.** Each response (STT → route → TTS → play) runs as one asyncio task, and capture
  is never blocked by it. On a wake word during *thinking* or *speaking*, `_interrupt()` cancels that task, awaits it,
  and writes its trace with outcome `barge_in`. Then the new interaction starts. Cancellation reaches the TTS
  iterator and the sink at their next `await`, so playback stops within one chunk. `cancel` (Stop button, Escape)
  takes the same path with outcome `cancelled`.
- A **1.5 s wake refractory period** (measured on capture timestamps) and `wake.reset()` after each detection
  prevent one utterance of "Hey Jarvis" from triggering twice. openWakeWord's score stays above the threshold for
  several 80 ms frames.
- The voice answer and the timer announcement go through the same `_speak` path. An unprompted announcement is
  spoken only if the assistant is idle. Otherwise it appears in the UI only, so it never talks over the user.

## Consequences

- Measured on this machine ([benchmarks](../benchmarks.md)), edge-tts' time to first byte is a network round trip
  plus Microsoft's synthesis start-up. That makes it the largest stage after STT, and the one with the widest p95,
  because it depends on the internet connection. Streaming keeps it at one round trip. A file-based design would
  also pay for the full synthesis and download.
- The answer text is sent to Microsoft's online service. This is stated in the README and the threat model. The
  `TextToSpeech` interface is the seam for a local engine when privacy or offline use matters more than voice
  quality. `GIDEON_TTS=simulated` already runs fully offline, though without intelligible audio.
- Barge-in tests (`test_wake_word_while_speaking_barges_in`, `test_cancel_while_speaking_stops_playback`) assert
  that the first answer is cut off (not all chunks sent), that its trace says `barge_in`/`cancelled`, and that a new
  interaction completes.
- Limitation: there is no echo cancellation. In live mode with loudspeakers, GIDEON could in principle wake itself
  if an answer contained the wake phrase. Headphones avoid this, and a future AEC stage would remove it.
