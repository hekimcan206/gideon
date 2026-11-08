import type { HelloClient, ServerMessage } from "../shared/protocol";
import { backoffDelay, DEFAULT_BACKOFF, type BackoffOptions } from "./backoff";
import { encode, parseServerMessage } from "./messages";

export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface ConnectionEvents {
  onMessage(message: ServerMessage): void;
  onStatus(status: ConnectionStatus, detail: { attempt: number; retryInMs?: number; reason?: string }): void;
}

/** The subset of the browser WebSocket the client uses, so tests can substitute a fake. */
export interface SocketLike {
  readyState: number;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number; reason: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export interface Timers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

export interface ConnectionOptions {
  url: string;
  token: string | null;
  client?: string;
  backoff?: BackoffOptions;
  heartbeatMs?: number;
  socketFactory?: (url: string) => SocketLike;
  timers?: Timers;
  random?: () => number;
}

const OPEN = 1;
// Close codes the backend uses for "do not retry with the same input".
const FATAL_CLOSE_CODES = new Set([4401, 4426]);

/**
 * WebSocket client with hello handshake, heartbeat and reconnect with jittered exponential back-off.
 *
 * The attempt counter resets only after the server's hello arrives (not on TCP open), so a backend that accepts the
 * socket and then crashes during start-up still backs off instead of being hammered.
 */
export class GideonConnection {
  private socket: SocketLike | null = null;
  private attempt = 0;
  private retryHandle: unknown = null;
  private heartbeatHandle: unknown = null;
  private awaitingPong: number | null = null;
  private nonce = 0;
  private stopped = false;
  private readonly timers: Timers;
  private readonly factory: (url: string) => SocketLike;

  constructor(
    private readonly options: ConnectionOptions,
    private readonly events: ConnectionEvents,
  ) {
    this.timers = options.timers ?? {
      setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
      clearTimeout: (h) => globalThis.clearTimeout(h as number),
      setInterval: (fn, ms) => globalThis.setInterval(fn, ms),
      clearInterval: (h) => globalThis.clearInterval(h as number),
    };
    this.factory = options.socketFactory ?? ((url) => new WebSocket(url) as unknown as SocketLike);
  }

  start(): void {
    this.stopped = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    this.socket?.close(1000, "client stopping");
    this.socket = null;
    this.events.onStatus("closed", { attempt: this.attempt });
  }

  send(message: Parameters<typeof encode>[0]): boolean {
    if (!this.socket || this.socket.readyState !== OPEN) return false;
    this.socket.send(encode(message));
    return true;
  }

  private open(): void {
    this.events.onStatus(this.attempt === 0 ? "connecting" : "reconnecting", { attempt: this.attempt });
    const socket = this.factory(this.options.url);
    this.socket = socket;
    socket.onopen = () => {
      const hello: HelloClient = { v: 1, type: "hello", client: this.options.client ?? "gideon-ui" };
      if (this.options.token) hello.token = this.options.token;
      socket.send(JSON.stringify(hello));
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      const parsed = parseServerMessage(event.data);
      if (!parsed.ok) {
        if (parsed.reason === "version_mismatch") socket.close(4426, "protocol version mismatch");
        return;
      }
      const message = parsed.message;
      if (message.type === "hello") {
        this.attempt = 0;
        this.startHeartbeat();
        this.events.onStatus("open", { attempt: 0 });
      } else if (message.type === "pong" && message.nonce === this.awaitingPong) {
        this.awaitingPong = null;
      }
      this.events.onMessage(message);
    };
    socket.onerror = () => {
      /* onclose follows and drives the retry */
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.clearTimers();
      if (this.stopped) return;
      if (FATAL_CLOSE_CODES.has(event.code)) {
        this.events.onStatus("closed", { attempt: this.attempt, reason: event.reason || String(event.code) });
        return;
      }
      const delay = backoffDelay(this.attempt, this.options.backoff ?? DEFAULT_BACKOFF, this.options.random);
      this.attempt += 1;
      this.events.onStatus("reconnecting", { attempt: this.attempt, retryInMs: delay });
      this.retryHandle = this.timers.setTimeout(() => this.open(), delay);
    };
  }

  /** A ping every heartbeatMs; an unanswered ping by the next tick means a half-open socket, so drop it. */
  private startHeartbeat(): void {
    const interval = this.options.heartbeatMs ?? 10_000;
    this.heartbeatHandle = this.timers.setInterval(() => {
      if (this.awaitingPong !== null) {
        this.socket?.close(4000, "heartbeat timeout");
        return;
      }
      this.nonce += 1;
      this.awaitingPong = this.nonce;
      this.send({ type: "ping", nonce: this.nonce });
    }, interval);
  }

  private clearTimers(): void {
    if (this.retryHandle !== null) this.timers.clearTimeout(this.retryHandle);
    if (this.heartbeatHandle !== null) this.timers.clearInterval(this.heartbeatHandle);
    this.retryHandle = null;
    this.heartbeatHandle = null;
    this.awaitingPong = null;
  }
}
