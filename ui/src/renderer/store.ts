import type { AssistantState, EngineInfo, ServerMessage, StageSpan } from "../shared/protocol";
import type { ConnectionStatus } from "./connection";

export interface Interaction {
  id: string;
  wakeScore: number | null;
  bargeIn: boolean;
  transcript: string | null;
  transcriptLanguage: string | null;
  typed: boolean;
  response: string | null;
  skill: string | null;
  intent: string | null;
  stages: StageSpan[];
  outcome: string | null;
  speechEndToFirstAudioMs: number | null;
  wakeToFirstAudioMs: number | null;
}

export interface UiState {
  connection: ConnectionStatus;
  retryInMs: number | null;
  attempt: number;
  assistant: AssistantState;
  activeId: string | null;
  serverVersion: string | null;
  engines: EngineInfo | null;
  skills: string[];
  interactions: Interaction[]; // newest first
  notifications: string[];
  lastError: string | null;
}

export type UiEvent =
  | { kind: "server"; message: ServerMessage }
  | { kind: "connection"; status: ConnectionStatus; attempt: number; retryInMs?: number };

export const MAX_INTERACTIONS = 25;
export const MAX_NOTIFICATIONS = 5;

export const initialState: UiState = {
  connection: "connecting",
  retryInMs: null,
  attempt: 0,
  assistant: "idle",
  activeId: null,
  serverVersion: null,
  engines: null,
  skills: [],
  interactions: [],
  notifications: [],
  lastError: null,
};

function blank(id: string): Interaction {
  return {
    id,
    wakeScore: null,
    bargeIn: false,
    transcript: null,
    transcriptLanguage: null,
    typed: false,
    response: null,
    skill: null,
    intent: null,
    stages: [],
    outcome: null,
    speechEndToFirstAudioMs: null,
    wakeToFirstAudioMs: null,
  };
}

/** Update (or create, newest first) the interaction with this id. Bounded so a long session cannot grow memory. */
function upsert(state: UiState, id: string, patch: Partial<Interaction>): UiState {
  const index = state.interactions.findIndex((i) => i.id === id);
  let interactions: Interaction[];
  if (index === -1) {
    interactions = [{ ...blank(id), ...patch }, ...state.interactions].slice(0, MAX_INTERACTIONS);
  } else {
    interactions = state.interactions.slice();
    interactions[index] = { ...state.interactions[index]!, ...patch };
  }
  return { ...state, interactions };
}

/** Pure reducer: every UI change is a function of (state, event), which is what the unit tests exercise. */
export function reduce(state: UiState, event: UiEvent): UiState {
  if (event.kind === "connection") {
    const next: UiState = {
      ...state,
      connection: event.status,
      attempt: event.attempt,
      retryInMs: event.retryInMs ?? null,
    };
    // The backend's state is unknown while disconnected; do not keep showing "speaking".
    return event.status === "open" ? next : { ...next, assistant: "idle", activeId: null };
  }

  const m = event.message;
  switch (m.type) {
    case "hello":
      return {
        ...state,
        assistant: m.state,
        serverVersion: m.version,
        engines: m.engines,
        skills: m.skills,
        lastError: null,
      };
    case "state":
      return { ...state, assistant: m.state, activeId: m.interaction_id ?? state.activeId };
    case "wake":
      return upsert({ ...state, activeId: m.interaction_id }, m.interaction_id, {
        wakeScore: m.score,
        bargeIn: m.barge_in ?? false,
      });
    case "transcript":
      return upsert(state, m.interaction_id, {
        transcript: m.text,
        transcriptLanguage: m.language,
        typed: m.source === "typed",
      });
    case "response":
      return upsert(state, m.interaction_id, {
        response: m.text,
        skill: m.skill,
        intent: m.intent,
      });
    case "trace":
      return upsert(state, m.interaction_id, {
        stages: m.stages,
        outcome: m.outcome,
        speechEndToFirstAudioMs: m.speech_end_to_first_audio_ms ?? null,
        wakeToFirstAudioMs: m.wake_to_first_audio_ms ?? null,
      });
    case "notification":
      return { ...state, notifications: [m.text, ...state.notifications].slice(0, MAX_NOTIFICATIONS) };
    case "error":
      return { ...state, lastError: `${m.code}: ${m.message}` };
    case "pong":
      return state;
  }
}

export interface LatencySummary {
  count: number;
  medianMs: number | null;
}

/** Median speech-end -> first-audio over the successful voice interactions currently on screen. */
export function latencySummary(interactions: Interaction[]): LatencySummary {
  const values = interactions
    .filter((i) => i.outcome === "ok" && i.speechEndToFirstAudioMs !== null)
    .map((i) => i.speechEndToFirstAudioMs as number)
    .sort((a, b) => a - b);
  if (values.length === 0) return { count: 0, medianMs: null };
  const mid = Math.floor(values.length / 2);
  const median = values.length % 2 ? values[mid]! : (values[mid - 1]! + values[mid]!) / 2;
  return { count: values.length, medianMs: median };
}
