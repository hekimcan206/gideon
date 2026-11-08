/**
 * Sandboxed preload: exposes one frozen, read-only config object to the renderer and nothing else. No ipcRenderer,
 * no Node APIs, no generic "send" function; the renderer talks to the backend over WebSocket like any client.
 */
import { contextBridge, ipcRenderer } from "electron";

const config: unknown = ipcRenderer.sendSync("gideon:config");

contextBridge.exposeInMainWorld("gideon", Object.freeze({ config }));
