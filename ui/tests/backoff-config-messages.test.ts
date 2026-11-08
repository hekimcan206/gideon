import { describe, expect, it } from "vitest";
import { backoffDelay } from "../src/renderer/backoff";
import { resolveConfig } from "../src/renderer/config";
import { encode, parseServerMessage } from "../src/renderer/messages";

describe("backoffDelay", () => {
  const opts = { baseMs: 250, capMs: 8_000 };

  it("grows exponentially up to the cap", () => {
    const max = (attempt: number) => backoffDelay(attempt, opts, () => 0.999999);
    expect([0, 1, 2, 3, 4, 5, 6, 10].map(max)).toEqual([249, 499, 999, 1999, 3999, 7999, 7999, 7999]);
  });

  it("uses full jitter: the whole range down to zero is possible", () => {
    expect(backoffDelay(5, opts, () => 0)).toBe(0);
    expect(backoffDelay(5, opts, () => 0.5)).toBe(4_000);
  });

  it("treats negative attempts as the first one", () => {
    expect(backoffDelay(-3, opts, () => 0.999999)).toBe(249);
  });
});

describe("resolveConfig", () => {
  it("prefers the Electron preload bridge", () => {
    const cfg = resolveConfig({ config: { wsUrl: "ws://127.0.0.1:19000", token: "t" } }, { hostname: "x", search: "" });
    expect(cfg).toEqual({ wsUrl: "ws://127.0.0.1:19000", token: "t" });
  });

  it("falls back to the loopback host in browser mode", () => {
    expect(resolveConfig(undefined, { hostname: "localhost", search: "" }).wsUrl).toBe("ws://localhost:18765");
    expect(resolveConfig(undefined, { hostname: "example.com", search: "" }).wsUrl).toBe("ws://127.0.0.1:18765");
  });

  it("accepts a ?ws= override only for loopback ws:// URLs", () => {
    const ok = resolveConfig(undefined, { hostname: "localhost", search: "?ws=ws://127.0.0.1:19999" });
    expect(ok.wsUrl).toBe("ws://127.0.0.1:19999");
    for (const bad of ["?ws=ws://evil.example:1", "?ws=wss://127.0.0.1:1", "?ws=not a url"]) {
      expect(resolveConfig(undefined, { hostname: "localhost", search: bad }).wsUrl).toBe("ws://localhost:18765");
    }
  });
});

describe("protocol messages", () => {
  it("accepts known server frames of the current version", () => {
    const parsed = parseServerMessage('{"v":1,"type":"pong","nonce":3}');
    expect(parsed).toEqual({ ok: true, message: { v: 1, type: "pong", nonce: 3 } });
  });

  it.each([
    ["{nope", "bad_json"],
    ["42", "bad_json"],
    ['{"type":"pong","nonce":1}', "version_mismatch"],
    ['{"v":2,"type":"pong","nonce":1}', "version_mismatch"],
    ['{"v":1,"type":"exec"}', "unknown_type"],
  ])("rejects %s as %s", (raw, reason) => {
    expect(parseServerMessage(raw)).toEqual({ ok: false, reason });
  });

  it("stamps the protocol version on outgoing frames", () => {
    expect(JSON.parse(encode({ type: "say", text: "saat kaç" }))).toEqual({ v: 1, type: "say", text: "saat kaç" });
  });
});
