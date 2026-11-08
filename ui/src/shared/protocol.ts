/* Generated from protocol/schema.json by json-schema-to-typescript. Do not edit; run npm run gen:protocol. */

/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "ServerMessage".
 */
export type ServerMessage =
  | HelloServer
  | StateChanged
  | WakeDetected
  | TranscriptMessage
  | ResponseMessage
  | TraceMessage
  | NotificationMessage
  | ErrorMessage
  | Pong;
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "AssistantState".
 */
export type AssistantState = "idle" | "listening" | "thinking" | "speaking";
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "Outcome".
 */
export type Outcome = "ok" | "no_speech" | "empty_transcript" | "barge_in" | "cancelled" | "error";
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "ClientMessage".
 */
export type ClientMessage = HelloClient | PushToTalk | Cancel | Say | Ping;

/**
 * GIDEON WebSocket protocol v1. Generated from gideon/protocol.py.
 */
export interface GideonProtocol {
  server?: ServerMessage;
  client?: ClientMessage;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "HelloServer".
 */
export interface HelloServer {
  v: 1;
  type: "hello";
  server: string;
  version: string;
  state: AssistantState;
  engines: EngineInfo;
  skills: string[];
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "EngineInfo".
 */
export interface EngineInfo {
  wake: string;
  vad: string;
  stt: string;
  tts: string;
  audio: string;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "StateChanged".
 */
export interface StateChanged {
  v: 1;
  type: "state";
  state: AssistantState;
  previous: AssistantState;
  interaction_id?: string | null;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "WakeDetected".
 */
export interface WakeDetected {
  v: 1;
  type: "wake";
  interaction_id: string;
  model: string;
  score: number;
  barge_in: boolean;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "TranscriptMessage".
 */
export interface TranscriptMessage {
  v: 1;
  type: "transcript";
  interaction_id: string;
  text: string;
  language: string;
  source: "speech" | "typed";
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "ResponseMessage".
 */
export interface ResponseMessage {
  v: 1;
  type: "response";
  interaction_id: string;
  skill: string;
  intent: string;
  text: string;
  language: string;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "TraceMessage".
 */
export interface TraceMessage {
  v: 1;
  type: "trace";
  interaction_id: string;
  outcome: Outcome;
  stages: StageSpan[];
  marks_ms: {
    [k: string]: number;
  };
  wake_to_first_audio_ms?: number | null;
  speech_end_to_first_audio_ms?: number | null;
}
/**
 * One bar of the latency waterfall, relative to the first mark of the interaction.
 *
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "StageSpan".
 */
export interface StageSpan {
  stage: string;
  start_ms: number;
  duration_ms: number;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "NotificationMessage".
 */
export interface NotificationMessage {
  v: 1;
  type: "notification";
  text: string;
  language: string;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "ErrorMessage".
 */
export interface ErrorMessage {
  v: 1;
  type: "error";
  code: string;
  message: string;
  interaction_id?: string | null;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "Pong".
 */
export interface Pong {
  v: 1;
  type: "pong";
  nonce: number;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "HelloClient".
 */
export interface HelloClient {
  v: 1;
  type: "hello";
  client: string;
  token?: string | null;
}
/**
 * Start listening without the wake word (UI button or hotkey).
 *
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "PushToTalk".
 */
export interface PushToTalk {
  v: 1;
  type: "ptt";
}
/**
 * Stop speaking/thinking now (manual barge-in).
 *
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "Cancel".
 */
export interface Cancel {
  v: 1;
  type: "cancel";
}
/**
 * A typed command: skips wake word, VAD and STT, runs the same skills and TTS.
 *
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "Say".
 */
export interface Say {
  v: 1;
  type: "say";
  text: string;
  language?: ("en" | "tr") | null;
}
/**
 * This interface was referenced by `GideonProtocol`'s JSON-Schema
 * via the `definition` "Ping".
 */
export interface Ping {
  v: 1;
  type: "ping";
  nonce: number;
}
