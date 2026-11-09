import { expect, test } from "@playwright/test";

/**
 * Drives the real stack: the backend replays the synthetic WAV utterances through openWakeWord, VAD,
 * faster-whisper and the skills, and the UI must show live state, transcripts and a latency waterfall.
 */
test("shows live voice interactions with a latency waterfall", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  page.on("pageerror", (err) => errors.push(err.message));

  await page.goto("/");
  await expect(page.locator("#conn")).toHaveText(/connected/, { timeout: 60_000 });
  await expect(page.locator("#engines")).toContainText("openwakeword");

  // Wait for a voice interaction that went all the way to an answer.
  await expect(page.locator(".turn .bot").first()).toBeVisible({ timeout: 150_000 });
  await expect(page.locator(".wf-row")).toHaveCount(7, { timeout: 60_000 });
  await expect(page.locator("#headline")).toContainText("end of speech → first audio");

  // A typed command goes through the same skills and TTS.
  await page.fill("#say-input", "saat kaç?");
  await page.press("#say-input", "Enter");
  await expect(page.locator(".turn .bot", { hasText: /Saat \d\d:\d\d/ }).first()).toBeVisible({ timeout: 30_000 });

  // Let the next voice interaction land so the waterfall shows a spoken (not typed) request.
  await expect(page.locator(".turn").first().locator(".who").first()).toHaveText("you", { timeout: 120_000 });
  await expect(page.locator('.wf-row[data-stage="wake_detect"]')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".wf-row")).toHaveCount(7);

  const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute("content");
  expect(csp).toContain("script-src 'self'");
  expect(errors).toEqual([]);

  if (process.env.GIDEON_SCREENSHOT) {
    await page.screenshot({ path: process.env.GIDEON_SCREENSHOT, fullPage: true });
  }
});
