import path from "node:path";
import { _electron as electron, expect, test } from "@playwright/test";

/**
 * Launches the real Electron app (built main + preload + renderer over app://) against a running backend and checks
 * the security posture from inside the renderer. Opt-in (needs a display and the Electron binary):
 *   npm run build && GIDEON_ELECTRON=1 npx playwright test e2e/electron.spec.ts
 */
test.skip(!process.env.GIDEON_ELECTRON, "set GIDEON_ELECTRON=1 to launch the desktop app");

test("desktop app: sandboxed renderer, live backend connection", async () => {
  const app = await electron.launch({ args: [path.resolve(__dirname, "..")] });
  try {
    const window = await app.firstWindow();
    await expect(window).toHaveURL("app://gideon/index.html");

    // No Node.js in the renderer, and the bridge exposes only the read-only config.
    const surface = await window.evaluate(() => ({
      require: typeof (globalThis as { require?: unknown }).require,
      process: typeof (globalThis as { process?: unknown }).process,
      bridgeKeys: Object.keys((window as unknown as { gideon?: object }).gideon ?? {}),
      bridgeFrozen: Object.isFrozen((window as unknown as { gideon?: object }).gideon),
    }));
    expect(surface).toEqual({ require: "undefined", process: "undefined", bridgeKeys: ["config"], bridgeFrozen: true });

    const prefs = await app.evaluate(({ BrowserWindow }) => {
      const wc = BrowserWindow.getAllWindows()[0]!.webContents;
      const p = (wc as unknown as { getLastWebPreferences(): Record<string, unknown> }).getLastWebPreferences();
      return { contextIsolation: p.contextIsolation, sandbox: p.sandbox, nodeIntegration: p.nodeIntegration };
    });
    expect(prefs).toEqual({ contextIsolation: true, sandbox: true, nodeIntegration: false });

    await expect(window.locator("#conn")).toHaveText(/connected/, { timeout: 60_000 });
    await expect(window.locator('.wf-row[data-stage="stt"]')).toBeVisible({ timeout: 150_000 });
    if (process.env.GIDEON_SCREENSHOT) await window.screenshot({ path: process.env.GIDEON_SCREENSHOT });
  } finally {
    await app.close();
  }
});
