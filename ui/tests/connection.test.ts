import { beforeEach, describe, expect, it } from "vitest";
import { GideonConnection, type ConnectionStatus, type SocketLike, type Timers } from "../src/renderer/connection";
import type { ServerMessage } from "../src/shared/protocol";

class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  closed: { code: number | undefined; reason: string | undefined } | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(code?: number, reason?: string): void {
    this.closed = { code, reason };
    this.serverClose(code ?? 1000, reason ?? "");
  }
  // test helpers
  serverOpen(): void {
    this.readyState = 1;
    this.onopen?.({});
  }
  serverSend(message: object): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  serverClose(code = 1006, reason = ""): void {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
  get frames(): Array<Record<string, unknown>> {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}

class ManualTimers implements Timers {
  timeouts: Array<{ fn: () => void; ms: number; id: number }> = [];
  intervals: Array<{ fn: () => void; ms: number; id: number }> = [];
  private next = 1;
  setTimeout(fn: () => void, ms: number): unknown {
    const id = this.next++;
    this.timeouts.push({ fn, ms, id });
    return id;
  }
  clearTimeout(handle: unknown): void {
    this.timeouts = this.timeouts.filter((t) => t.id !== handle);
  }
  setInterval(fn: () => void, ms: number): unknown {
    const id = this.next++;
    this.intervals.push({ fn, ms, id });
    return id;
  }
  clearInterval(handle: unknown): void {
    this.intervals = this.intervals.filter((t) => t.id !== handle);
  }
  fireTimeout(): number {
    const t = this.timeouts.shift();
    if (!t) throw new Error("no timeout pending");
    t.fn();
    return t.ms;
  }
  tickIntervals(): void {
    for (const t of [...this.intervals]) t.fn();
  }
}

const HELLO = {
  v: 1,
  type: "hello",
  server: "gideon",
  version: "1.0.0",
  state: "idle",
  engines: { wake: "w", vad: "v", stt: "s", tts: "t", audio: "a" },
  skills: ["clock"],
};

function setup(token: string | null = "tok") {
  FakeSocket.instances = [];
  const timers = new ManualTimers();
  const statuses: Array<[ConnectionStatus, number, number | undefined]> = [];
  const messages: ServerMessage[] = [];
  const connection = new GideonConnection(
    {
      url: "ws://127.0.0.1:18765",
      token,
      socketFactory: (url) => new FakeSocket(url),
      timers,
      random: () => 0.5,
      backoff: { baseMs: 100, capMs: 1_000 },
      heartbeatMs: 5_000,
    },
    {
      onMessage: (m) => messages.push(m),
      onStatus: (status, detail) => statuses.push([status, detail.attempt, detail.retryInMs]),
    },
  );
  return { connection, timers, statuses, messages, socket: () => FakeSocket.instances.at(-1)! };
}

describe("GideonConnection", () => {
  let ctx: ReturnType<typeof setup>;
  beforeEach(() => {
    ctx = setup();
  });

  it("sends hello with the token on open and reports open only after the server's hello", () => {
    ctx.connection.start();
    ctx.socket().serverOpen();
    expect(ctx.socket().frames).toEqual([{ v: 1, type: "hello", client: "gideon-ui", token: "tok" }]);
    expect(ctx.statuses.map((s) => s[0])).toEqual(["connecting"]);
    ctx.socket().serverSend(HELLO);
    expect(ctx.statuses.at(-1)?.[0]).toBe("open");
    expect(ctx.messages[0]?.type).toBe("hello");
  });

  it("omits the token when there is none", () => {
    const c = setup(null);
    c.connection.start();
    c.socket().serverOpen();
    expect(c.socket().frames[0]).toEqual({ v: 1, type: "hello", client: "gideon-ui" });
  });

  it("reconnects with growing jittered delays and resets after a successful hello", () => {
    ctx.connection.start();
    ctx.socket().serverClose();
    expect(ctx.timers.fireTimeout()).toBe(50); // 0.5 * min(1000, 100 * 2^0)
    ctx.socket().serverClose();
    expect(ctx.timers.fireTimeout()).toBe(100);
    ctx.socket().serverClose();
    expect(ctx.timers.fireTimeout()).toBe(200);
    ctx.socket().serverOpen();
    ctx.socket().serverSend(HELLO);
    ctx.socket().serverClose();
    expect(ctx.timers.fireTimeout()).toBe(50);
    expect(FakeSocket.instances).toHaveLength(5);
    expect(ctx.statuses.filter((s) => s[0] === "reconnecting" && s[2] !== undefined).map((s) => s[1])).toEqual([
      1, 2, 3, 1,
    ]);
  });

  it("caps the delay", () => {
    ctx.connection.start();
    let last = 0;
    for (let i = 0; i < 10; i++) {
      ctx.socket().serverClose();
      last = ctx.timers.fireTimeout();
    }
    expect(last).toBe(500); // 0.5 * cap
  });

  it("does not retry after an auth or version rejection", () => {
    ctx.connection.start();
    ctx.socket().serverClose(4401, "unauthorized");
    expect(ctx.timers.timeouts).toHaveLength(0);
    expect(ctx.statuses.at(-1)?.[0]).toBe("closed");
  });

  it("closes on a protocol version mismatch instead of misreading frames", () => {
    ctx.connection.start();
    ctx.socket().serverOpen();
    ctx.socket().serverSend({ ...HELLO, v: 2 });
    expect(ctx.socket().closed?.code).toBe(4426);
    expect(ctx.messages).toHaveLength(0);
  });

  it("drops a half-open socket when a ping goes unanswered", () => {
    ctx.connection.start();
    const socket = ctx.socket();
    socket.serverOpen();
    socket.serverSend(HELLO);
    ctx.timers.tickIntervals();
    expect(socket.frames.at(-1)).toEqual({ v: 1, type: "ping", nonce: 1 });
    socket.serverSend({ v: 1, type: "pong", nonce: 1 });
    ctx.timers.tickIntervals();
    expect(socket.frames.at(-1)).toEqual({ v: 1, type: "ping", nonce: 2 });
    ctx.timers.tickIntervals(); // no pong for nonce 2
    expect(socket.closed?.code).toBe(4000);
    expect(ctx.timers.timeouts).toHaveLength(1); // reconnect scheduled
  });

  it("send() refuses while disconnected and stop() prevents reconnects", () => {
    ctx.connection.start();
    expect(ctx.connection.send({ type: "ptt" })).toBe(false);
    ctx.socket().serverOpen();
    expect(ctx.connection.send({ type: "ptt" })).toBe(true);
    ctx.connection.stop();
    expect(ctx.timers.timeouts).toHaveLength(0);
    expect(ctx.statuses.at(-1)?.[0]).toBe("closed");
  });

  it("ignores binary frames and unknown message types", () => {
    ctx.connection.start();
    ctx.socket().serverOpen();
    ctx.socket().onmessage?.({ data: new ArrayBuffer(4) });
    ctx.socket().serverSend({ v: 1, type: "exec", cmd: "rm" });
    expect(ctx.messages).toHaveLength(0);
  });
});

