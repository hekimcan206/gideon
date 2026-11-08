/**
 * Electron main process.
 *
 * Security baseline (Electron security checklist): the renderer gets no Node.js (nodeIntegration off), runs in the
 * Chromium sandbox with context isolation, loads only bundled files over a custom app:// scheme (no file://, no
 * remote content), is served with a strict CSP, cannot navigate or open windows, and every permission request
 * (microphone included: audio is captured by the Python backend, not the renderer) is denied. The only bridge is a
 * read-only config object exposed by the preload script.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { app, BrowserWindow, ipcMain, net, protocol, session, shell } from "electron";

const SCHEME = "app";
const HOST = "gideon";
const ORIGIN = `${SCHEME}://${HOST}`;
const RENDERER_DIR = path.join(__dirname, "..", "renderer");
const BACKEND_PORT = Number(process.env.GIDEON_PORT ?? "18765");

const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  `connect-src ws://127.0.0.1:${BACKEND_PORT} ws://localhost:${BACKEND_PORT}`,
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
  "object-src 'none'",
].join("; ");

// A fresh random token per launch when Electron starts the backend itself: a local web page or another user's
// process cannot guess it, and it never touches disk.
const spawnBackend = process.env.GIDEON_SPAWN_BACKEND === "1";
const token = process.env.GIDEON_TOKEN ?? (spawnBackend ? randomBytes(24).toString("base64url") : "");

export interface RendererConfig {
  wsUrl: string;
  token: string | null;
  platform: string;
  appVersion: string;
}

const rendererConfig: RendererConfig = {
  wsUrl: `ws://127.0.0.1:${BACKEND_PORT}`,
  token: token || null,
  platform: process.platform,
  appVersion: app.getVersion(),
};

protocol.registerSchemesAsPrivileged([
  { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

let backend: ChildProcess | null = null;

function startBackend(): void {
  const python = process.env.GIDEON_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
  backend = spawn(python, ["-m", "gideon", "serve"], {
    cwd: process.env.GIDEON_BACKEND_DIR ?? path.join(__dirname, "..", "..", "..", "backend"),
    env: { ...process.env, GIDEON_TOKEN: token, GIDEON_PORT: String(BACKEND_PORT), GIDEON_HOST: "127.0.0.1" },
    stdio: ["ignore", "inherit", "inherit"],
    windowsHide: true,
  });
  backend.on("exit", (code) => {
    console.error(`backend exited with code ${code}`);
    backend = null;
  });
}

/** Serve bundled renderer files only; anything resolving outside RENDERER_DIR is a 404. */
function registerAppProtocol(): void {
  protocol.handle(SCHEME, async (request) => {
    const url = new URL(request.url);
    if (url.host !== HOST) return new Response("not found", { status: 404 });
    const relative = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
    const file = path.normalize(path.join(RENDERER_DIR, relative));
    if (!file.startsWith(RENDERER_DIR + path.sep)) return new Response("not found", { status: 404 });
    const response = await net.fetch(pathToFileURL(file).toString());
    const headers = new Headers(response.headers);
    headers.set("Content-Security-Policy", CSP);
    headers.set("X-Content-Type-Options", "nosniff");
    return new Response(response.body, { status: response.status, headers });
  });
}

function hardenSession(): void {
  session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
}

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 720,
    minHeight: 520,
    backgroundColor: "#0d1117",
    title: "GIDEON",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      spellcheck: false,
    },
  });
  win.once("ready-to-show", () => win.show());
  // No navigation away from the bundle and no new windows; external https links go to the OS browser.
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(`${ORIGIN}/`)) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  void win.loadURL(`${ORIGIN}/index.html`);
  return win;
}

// The preload asks for its config synchronously; answer only our own top-level frame.
ipcMain.on("gideon:config", (event) => {
  const frameUrl = event.senderFrame?.url ?? "";
  event.returnValue = frameUrl.startsWith(`${ORIGIN}/`) ? rendererConfig : null;
});

app.enableSandbox();
app.whenReady().then(() => {
  hardenSession();
  registerAppProtocol();
  if (spawnBackend) startBackend();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("web-contents-created", (_event, contents) => {
  contents.on("will-attach-webview", (event) => event.preventDefault());
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  backend?.kill();
});
