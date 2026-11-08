import { latencySummary, type Interaction, type UiState } from "./store";
import { formatMs, layoutWaterfall } from "./waterfall";

/**
 * DOM rendering. Every server-supplied string is written with textContent, never innerHTML: transcripts and
 * answers are untrusted text (a note can contain anything a user said), and the CSP has no 'unsafe-inline'.
 */
function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function byId<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`#${id} missing`);
  return node as T;
}

const CONNECTION_TEXT: Record<UiState["connection"], string> = {
  connecting: "connecting…",
  open: "connected",
  reconnecting: "reconnecting",
  closed: "disconnected",
};

export function renderConnection(state: UiState): void {
  const conn = byId("conn");
  let text = CONNECTION_TEXT[state.connection];
  if (state.connection === "reconnecting" && state.retryInMs !== null) {
    text += ` in ${(state.retryInMs / 1000).toFixed(1)} s (attempt ${state.attempt})`;
  }
  if (state.connection === "open" && state.serverVersion) text += ` · backend ${state.serverVersion}`;
  conn.textContent = text;
  conn.dataset.status = state.connection;
}

export function renderState(state: UiState): void {
  document.body.dataset.state = state.assistant;
  byId("state-label").textContent = state.connection === "open" ? state.assistant : "offline";
  const ptt = byId<HTMLButtonElement>("ptt");
  const stop = byId<HTMLButtonElement>("stop");
  ptt.disabled = state.connection !== "open" || state.assistant === "listening";
  stop.disabled = state.connection !== "open" || state.assistant === "idle";
}

export function renderEngines(state: UiState): void {
  const dl = byId("engines");
  dl.replaceChildren();
  if (!state.engines) return;
  for (const [key, value] of Object.entries(state.engines)) {
    dl.append(el("dt", undefined, key), el("dd", undefined, value));
  }
}

function waterfallFor(state: UiState): Interaction | undefined {
  return state.interactions.find((i) => i.stages.length > 0);
}

export function renderWaterfall(state: UiState): void {
  const box = byId("waterfall");
  const headline = byId("headline");
  const interaction = waterfallFor(state);
  box.replaceChildren();
  if (!interaction) {
    box.append(el("p", "empty", "No interaction yet."));
    headline.textContent = "";
    return;
  }
  for (const bar of layoutWaterfall(interaction.stages)) {
    const row = el("div", "wf-row");
    row.dataset.stage = bar.stage;
    const track = el("div", "wf-track");
    const fill = el("div", `wf-bar k-${bar.kind}`);
    fill.style.marginLeft = `${bar.leftPct}%`;
    fill.style.width = `${bar.widthPct}%`;
    fill.title = `${bar.label}: ${bar.text}`;
    track.append(fill);
    row.append(el("span", "wf-label", bar.label), track, el("span", "wf-value", bar.text));
    box.append(row);
  }
  const parts: string[] = [];
  if (interaction.speechEndToFirstAudioMs !== null) {
    parts.push(`end of speech → first audio ${formatMs(interaction.speechEndToFirstAudioMs)}`);
  }
  if (interaction.outcome && interaction.outcome !== "ok") parts.push(interaction.outcome.replace("_", " "));
  headline.textContent = parts.join(" · ");
}

export function renderTranscript(state: UiState): void {
  const list = byId("transcript");
  list.replaceChildren();
  for (const i of state.interactions) {
    const item = el("li", "turn");
    if (i.id === state.activeId && state.assistant !== "idle") item.classList.add("active");
    const you = el("p", "you");
    you.append(el("span", "who", i.typed ? "typed" : "you"));
    you.append(el("span", "text", i.transcript ?? (i.outcome === "no_speech" ? "(no speech)" : "…")));
    if (i.transcriptLanguage) you.append(el("span", "tag", i.transcriptLanguage));
    item.append(you);
    if (i.response !== null) {
      const bot = el("p", "bot");
      bot.append(el("span", "who", "gideon"), el("span", "text", i.response));
      if (i.skill) bot.append(el("span", "tag", `${i.skill}/${i.intent ?? ""}`));
      item.append(bot);
    }
    const meta: string[] = [];
    if (i.wakeScore !== null) meta.push(`wake ${i.wakeScore.toFixed(2)}`);
    if (i.bargeIn) meta.push("barge-in");
    if (i.speechEndToFirstAudioMs !== null) meta.push(`${formatMs(i.speechEndToFirstAudioMs)} to first audio`);
    if (i.outcome && i.outcome !== "ok") meta.push(i.outcome.replace("_", " "));
    if (meta.length) item.append(el("p", "meta", meta.join(" · ")));
    list.append(item);
  }
  const summary = latencySummary(state.interactions);
  byId("summary").textContent =
    summary.medianMs === null ? "" : `median ${formatMs(summary.medianMs)} over ${summary.count}`;
}

export function renderNotices(state: UiState): void {
  const toasts = byId("toasts");
  toasts.replaceChildren(...state.notifications.slice(0, 3).map((n) => el("p", "toast", n)));
  const error = byId("error");
  error.hidden = state.lastError === null;
  error.textContent = state.lastError ?? "";
}

export function render(state: UiState): void {
  renderConnection(state);
  renderState(state);
  renderEngines(state);
  renderWaterfall(state);
  renderTranscript(state);
  renderNotices(state);
}
