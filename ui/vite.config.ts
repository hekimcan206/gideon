import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

/**
 * Content-Security-Policy for the built renderer. Applied only to production builds: the Vite dev server injects
 * inline styles for hot reload, which a strict style-src would block. The Electron main process sends the same
 * policy as an HTTP header for app:// responses, so the packaged app is covered twice.
 */
export const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src ws://127.0.0.1:18765 ws://localhost:18765",
  "base-uri 'none'",
  "form-action 'none'",
  // frame-ancestors is ignored in a meta tag; the Electron main process sends it as a header.
  "object-src 'none'",
].join("; ");

function cspMeta(): Plugin {
  return {
    name: "gideon-csp",
    apply: "build",
    transformIndexHtml: () => [
      { tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: CSP }, injectTo: "head-prepend" },
    ],
  };
}

export default defineConfig({
  base: "./",
  plugins: [cspMeta()],
  build: {
    outDir: "dist/renderer",
    emptyOutDir: true,
    target: "es2023",
    sourcemap: true,
  },
  server: { port: 5173, strictPort: true, host: "127.0.0.1" },
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.test.ts"],
    coverage: { provider: "v8", include: ["src/renderer/**/*.ts"], exclude: ["src/renderer/main.ts"] },
  },
});
