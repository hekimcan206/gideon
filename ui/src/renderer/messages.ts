import type { ClientMessage, ServerMessage } from "../shared/protocol";

export const PROTOCOL_VERSION = 1;

const SERVER_TYPES = new Set<ServerMessage["type"]>([
  "hello",
  "state",
  "wake",
  "transcript",
  "response",
  "trace",
  "notification",
  "error",
  "pong",
]);

export type ParseResult =
  | { ok: true; message: ServerMessage }
  | { ok: false; reason: "bad_json" | "version_mismatch" | "unknown_type" };

/**
 * Structural check of an inbound frame. The backend validates with pydantic and the types are generated from the
 * same schema, so a full JSON-schema validator in the renderer would only re-check our own server; the version and
 * discriminator are what actually protect the UI from a newer/older backend.
 */
export function parseServerMessage(raw: string): ParseResult {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { ok: false, reason: "bad_json" };
  }
  if (typeof data !== "object" || data === null) return { ok: false, reason: "bad_json" };
  const frame = data as { v?: unknown; type?: unknown };
  if (frame.v !== PROTOCOL_VERSION) return { ok: false, reason: "version_mismatch" };
  if (typeof frame.type !== "string" || !SERVER_TYPES.has(frame.type as ServerMessage["type"])) {
    return { ok: false, reason: "unknown_type" };
  }
  return { ok: true, message: data as ServerMessage };
}

type Body<T> = T extends { v?: unknown } ? Omit<T, "v"> : never;

export function encode(message: Body<ClientMessage>): string {
  return JSON.stringify({ v: PROTOCOL_VERSION, ...message });
}
