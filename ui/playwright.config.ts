import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end check of the renderer in browser mode against a running backend (the docker compose demo by default).
 * It is the same bundle Electron loads over app://; only the preload bridge is absent.
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.GIDEON_UI_URL ?? "http://localhost:18766",
    viewport: { width: 1280, height: 820 },
    colorScheme: "dark",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 820 } } }],
});
