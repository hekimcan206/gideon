import { resolveConfig } from "./config";
import { GideonConnection } from "./connection";
import { initialState, reduce, type UiEvent, type UiState } from "./store";
import { render } from "./view";

declare global {
  interface Window {
    gideon?: { config?: { wsUrl?: string; token?: string | null } | null };
  }
}

const config = resolveConfig(window.gideon, window.location);
let state: UiState = initialState;
let frame = 0;

function dispatch(event: UiEvent): void {
  state = reduce(state, event);
  // Coalesce bursts (state + transcript + trace arrive within a millisecond) into one render per frame.
  if (frame === 0) {
    frame = requestAnimationFrame(() => {
      frame = 0;
      render(state);
    });
  }
}

const connection = new GideonConnection(
  { url: config.wsUrl, token: config.token },
  {
    onMessage: (message) => dispatch({ kind: "server", message }),
    onStatus: (status, detail) =>
      dispatch({
        kind: "connection",
        status,
        attempt: detail.attempt,
        ...(detail.retryInMs !== undefined ? { retryInMs: detail.retryInMs } : {}),
      }),
  },
);

document.getElementById("ptt")?.addEventListener("click", () => connection.send({ type: "ptt" }));
document.getElementById("stop")?.addEventListener("click", () => connection.send({ type: "cancel" }));
document.getElementById("say-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.getElementById("say-input") as HTMLInputElement;
  const text = input.value.trim();
  if (text && connection.send({ type: "say", text })) input.value = "";
});
window.addEventListener("keydown", (event) => {
  // Space = push to talk, Escape = stop, unless typing in the command box.
  if ((event.target as HTMLElement | null)?.tagName === "INPUT") return;
  if (event.code === "Space") {
    event.preventDefault();
    connection.send({ type: "ptt" });
  } else if (event.code === "Escape") {
    connection.send({ type: "cancel" });
  }
});

render(state);
connection.start();
