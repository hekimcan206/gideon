import type { StageSpan } from "../shared/protocol";

export type StageKind = "user" | "audio" | "compute" | "network" | "output";

/**
 * How each stage is coloured. "utterance" is the user talking, not latency the system adds, so it is drawn
 * differently and excluded from the headline number.
 */
export const STAGE_KIND: Record<string, StageKind> = {
  wake_detect: "audio",
  utterance: "user",
  endpoint: "audio",
  stt: "compute",
  route: "compute",
  tts_first_byte: "network",
  playback_start: "output",
  playback: "output",
};

export const STAGE_LABEL: Record<string, string> = {
  wake_detect: "wake word",
  utterance: "user speaking",
  endpoint: "end-of-speech wait",
  stt: "speech-to-text",
  route: "intent + skill",
  tts_first_byte: "TTS first byte",
  playback_start: "playback start",
  playback: "playback",
};

export interface Bar {
  stage: string;
  label: string;
  kind: StageKind;
  leftPct: number;
  widthPct: number;
  durationMs: number;
  text: string;
}

export function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms >= 10) return `${Math.round(ms)} ms`;
  return `${ms.toFixed(1)} ms`;
}

/**
 * Lay out stage spans as percentage-positioned bars. Playback is left out by default: it lasts as long as the
 * answer and would squash every latency bar into a sliver. Zero-length stages get a minimum visible width.
 */
export function layoutWaterfall(stages: StageSpan[], options: { includePlayback?: boolean; minWidthPct?: number } = {}): Bar[] {
  const visible = stages.filter((s) => options.includePlayback || s.stage !== "playback");
  if (visible.length === 0) return [];
  const origin = Math.min(...visible.map((s) => s.start_ms));
  const end = Math.max(...visible.map((s) => s.start_ms + s.duration_ms));
  const span = Math.max(end - origin, 1);
  const minWidth = options.minWidthPct ?? 0.6;
  return visible.map((s) => {
    const leftPct = ((s.start_ms - origin) / span) * 100;
    const widthPct = Math.max((s.duration_ms / span) * 100, minWidth);
    return {
      stage: s.stage,
      label: STAGE_LABEL[s.stage] ?? s.stage,
      kind: STAGE_KIND[s.stage] ?? "compute",
      leftPct: Math.min(leftPct, 100 - minWidth),
      widthPct: Math.min(widthPct, 100 - Math.min(leftPct, 100 - minWidth)),
      durationMs: s.duration_ms,
      text: formatMs(s.duration_ms),
    };
  });
}
