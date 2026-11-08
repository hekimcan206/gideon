import { describe, expect, it } from "vitest";
import type { ServerMessage, StageSpan } from "../src/shared/protocol";
import { initialState, latencySummary, MAX_INTERACTIONS, reduce, type UiState } from "../src/renderer/store";
import { formatMs, layoutWaterfall } from "../src/renderer/waterfall";

const server = (message: ServerMessage) => ({ kind: "server", message }) as const;

const STAGES: StageSpan[] = [
  { stage: "wake_detect", start_ms: 0, duration_ms: 2 },
  { stage: "utterance", start_ms: 2, duration_ms: 1500 },
  { stage: "endpoint", start_ms: 1502, duration_ms: 560 },
  { stage: "stt", start_ms: 2062, duration_ms: 700 },
  { stage: "route", start_ms: 2762, duration_ms: 0.4 },
  { stage: "tts_first_byte", start_ms: 2762.4, duration_ms: 500 },
  { stage: "playback_start", start_ms: 3262.4, duration_ms: 0.1 },
  { stage: "playback", start_ms: 3262.5, duration_ms: 2000 },
];

function interaction(state: UiState, id: string, latency: number | null = 1760, outcome = "ok"): UiState {
  let s = reduce(state, server({ v: 1, type: "wake", interaction_id: id, model: "m", score: 0.91, barge_in: false }));
  s = reduce(s, server({ v: 1, type: "transcript", interaction_id: id, text: "saat kaç", language: "tr", source: "speech" }));
  s = reduce(s, server({ v: 1, type: "response", interaction_id: id, skill: "clock", intent: "time.now", text: "Saat 14:05.", language: "tr" }));
  return reduce(s, server({
    v: 1, type: "trace", interaction_id: id, outcome: outcome as "ok", stages: STAGES, marks_ms: {},
    speech_end_to_first_audio_ms: latency, wake_to_first_audio_ms: latency === null ? null : latency + 1500,
  }));
}

describe("reduce", () => {
  it("builds one interaction from wake, transcript, response and trace", () => {
    const s = interaction(initialState, "a1");
    expect(s.interactions).toHaveLength(1);
    expect(s.interactions[0]).toMatchObject({
      id: "a1", wakeScore: 0.91, transcript: "saat kaç", transcriptLanguage: "tr", response: "Saat 14:05.",
      skill: "clock", intent: "time.now", outcome: "ok", speechEndToFirstAudioMs: 1760, typed: false,
    });
    expect(s.activeId).toBe("a1");
  });

  it("keeps newest first and bounds the history", () => {
    let s = initialState;
    for (let i = 0; i < MAX_INTERACTIONS + 5; i++) s = interaction(s, `i${i}`);
    expect(s.interactions).toHaveLength(MAX_INTERACTIONS);
    expect(s.interactions[0]?.id).toBe(`i${MAX_INTERACTIONS + 4}`);
  });

  it("tracks assistant state and resets it when the connection drops", () => {
    let s = reduce(initialState, server({ v: 1, type: "state", state: "speaking", previous: "thinking", interaction_id: "x" }));
    expect(s.assistant).toBe("speaking");
    s = reduce(s, { kind: "connection", status: "reconnecting", attempt: 2, retryInMs: 400 });
    expect(s).toMatchObject({ assistant: "idle", connection: "reconnecting", attempt: 2, retryInMs: 400, activeId: null });
  });

  it("takes engines, skills and state from hello and clears a stale error", () => {
    let s = reduce(initialState, server({ v: 1, type: "error", code: "pipeline_error", message: "RuntimeError" }));
    expect(s.lastError).toBe("pipeline_error: RuntimeError");
    s = reduce(s, server({
      v: 1, type: "hello", server: "gideon", version: "1.0.0", state: "listening", skills: ["clock", "timer"],
      engines: { wake: "openwakeword:hey_jarvis", vad: "webrtcvad:2", stt: "faster-whisper:base:int8:t8", tts: "edge-tts", audio: "wav" },
    }));
    expect(s).toMatchObject({ assistant: "listening", serverVersion: "1.0.0", skills: ["clock", "timer"], lastError: null });
  });

  it("marks typed commands and keeps the latest notifications", () => {
    let s = reduce(initialState, server({ v: 1, type: "transcript", interaction_id: "t", text: "hi", language: "en", source: "typed" }));
    expect(s.interactions[0]?.typed).toBe(true);
    for (let i = 0; i < 8; i++) s = reduce(s, server({ v: 1, type: "notification", text: `n${i}`, language: "en" }));
    expect(s.notifications).toEqual(["n7", "n6", "n5", "n4", "n3"]);
  });

  it("ignores pongs", () => {
    expect(reduce(initialState, server({ v: 1, type: "pong", nonce: 1 }))).toBe(initialState);
  });
});

describe("latencySummary", () => {
  it("is the median over successful interactions only", () => {
    let s = initialState;
    for (const [i, ms] of [1200, 1500, 900, 4000].entries()) s = interaction(s, `ok${i}`, ms);
    s = interaction(s, "barge", 50, "barge_in");
    s = interaction(s, "typed", null);
    expect(latencySummary(s.interactions)).toEqual({ count: 4, medianMs: 1350 });
    expect(latencySummary([])).toEqual({ count: 0, medianMs: null });
  });
});

describe("layoutWaterfall", () => {
  it("scales bars to the span without playback and keeps tiny stages visible", () => {
    const bars = layoutWaterfall(STAGES);
    expect(bars.map((b) => b.stage)).toEqual([
      "wake_detect", "utterance", "endpoint", "stt", "route", "tts_first_byte", "playback_start",
    ]);
    const span = 3262.5;
    const stt = bars.find((b) => b.stage === "stt")!;
    expect(stt.leftPct).toBeCloseTo((2062 / span) * 100, 5);
    expect(stt.widthPct).toBeCloseTo((700 / span) * 100, 5);
    expect(bars.find((b) => b.stage === "route")!.widthPct).toBe(0.6);
    expect(bars.find((b) => b.stage === "utterance")!.kind).toBe("user");
    for (const b of bars) expect(b.leftPct + b.widthPct).toBeLessThanOrEqual(100.0001);
  });

  it("can include playback and handles empty input", () => {
    expect(layoutWaterfall(STAGES, { includePlayback: true })).toHaveLength(8);
    expect(layoutWaterfall([])).toEqual([]);
  });

  it("formats durations", () => {
    expect([formatMs(0.42), formatMs(12.6), formatMs(999), formatMs(1760)]).toEqual(["0.4 ms", "13 ms", "999 ms", "1.76 s"]);
  });
});
