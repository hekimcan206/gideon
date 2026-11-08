import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { initialState, reduce, type UiState } from "../src/renderer/store";
import { render } from "../src/renderer/view";

const html = readFileSync(resolve(__dirname, "../index.html"), "utf8");

beforeEach(() => {
  // Drop the module script: the test renders the markup only, it does not boot the app.
  document.body.innerHTML = html
    .slice(html.indexOf("<body>") + 6, html.indexOf("</body>"))
    .replace(/<script[\s\S]*?<\/script>/g, "");
});

function withInteraction(text: string): UiState {
  let s = reduce(initialState, { kind: "connection", status: "open", attempt: 0 });
  s = reduce(s, { kind: "server", message: { v: 1, type: "transcript", interaction_id: "x", text, language: "en", source: "speech" } });
  return reduce(s, {
    kind: "server",
    message: {
      v: 1, type: "trace", interaction_id: "x", outcome: "ok", marks_ms: {}, speech_end_to_first_audio_ms: 1234,
      stages: [{ stage: "stt", start_ms: 0, duration_ms: 700 }, { stage: "tts_first_byte", start_ms: 700, duration_ms: 534 }],
    },
  });
}

describe("render", () => {
  it("writes untrusted text as text, never as markup", () => {
    render(withInteraction('<img src=x onerror="alert(1)">'));
    const transcript = document.getElementById("transcript")!;
    expect(transcript.querySelector("img")).toBeNull();
    expect(transcript.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it("draws one waterfall row per stage with the headline latency", () => {
    render(withInteraction("what time is it"));
    const rows = document.querySelectorAll(".wf-row");
    expect(rows).toHaveLength(2);
    expect((rows[0] as HTMLElement).dataset.stage).toBe("stt");
    expect(document.getElementById("headline")!.textContent).toBe("end of speech → first audio 1.23 s");
    expect(document.getElementById("summary")!.textContent).toBe("median 1.23 s over 1");
  });

  it("reflects connection and assistant state in the controls", () => {
    render(initialState);
    expect((document.getElementById("ptt") as HTMLButtonElement).disabled).toBe(true);
    expect(document.getElementById("state-label")!.textContent).toBe("offline");
    let s = reduce(initialState, { kind: "connection", status: "open", attempt: 0 });
    s = reduce(s, { kind: "server", message: { v: 1, type: "state", state: "speaking", previous: "thinking" } });
    render(s);
    expect(document.body.dataset.state).toBe("speaking");
    expect((document.getElementById("stop") as HTMLButtonElement).disabled).toBe(false);
    render(reduce(s, { kind: "connection", status: "reconnecting", attempt: 3, retryInMs: 1500 }));
    expect(document.getElementById("conn")!.textContent).toBe("reconnecting in 1.5 s (attempt 3)");
  });
});
