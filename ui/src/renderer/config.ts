export interface RendererConfig {
  wsUrl: string;
  token: string | null;
}

interface Bridge {
  config?: Partial<RendererConfig> | null;
}

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

/**
 * Electron: the preload exposes { wsUrl, token } on window.gideon. Browser mode (Vite dev server or the compose
 * demo): no bridge, so connect to the backend on the same loopback host. A ?ws= override is honoured only for
 * loopback hosts (the CSP would block anything else anyway).
 */
export function resolveConfig(bridge: Bridge | undefined, location: { hostname: string; search: string }): RendererConfig {
  if (bridge?.config?.wsUrl) {
    return { wsUrl: bridge.config.wsUrl, token: bridge.config.token ?? null };
  }
  const override = new URLSearchParams(location.search).get("ws");
  if (override) {
    try {
      const url = new URL(override);
      if (url.protocol === "ws:" && LOOPBACK.has(url.hostname)) return { wsUrl: url.toString().replace(/\/$/, ""), token: null };
    } catch {
      /* fall through to the default */
    }
  }
  const host = LOOPBACK.has(location.hostname) ? location.hostname : "127.0.0.1";
  return { wsUrl: `ws://${host}:18765`, token: null };
}
