import React from "react";
import { createRoot } from "react-dom/client";
import * as ExcalidrawLib from "https://esm.sh/@excalidraw/excalidraw@0.18.1/dist/prod/index.js?external=react,react-dom";

const { Excalidraw, exportToBlob, convertToExcalidrawElements, CaptureUpdateAction } = ExcalidrawLib;
window.ExcalidrawLib = ExcalidrawLib;

const $ = (id) => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

function api(path, opts = {}) {
  return fetch(path, opts).then(async (r) => {
    if (r.status === 401) {
      location.replace("/login.html");
      throw new Error("login required");
    }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    return body;
  });
}

function setStatus(msg, isError = false) {
  $("status").textContent = msg;
  $("status").className = isError ? "error" : "";
}

// ---- session list ----
let hosts = [];     // last /api/sessions result, also used by the image browser
let selected = null; // {host, id}
try { selected = JSON.parse(store.get("sketchpad-selected")); } catch {}

function findSession(sel) {
  const h = sel && hosts.find((x) => x.host === sel.host);
  const s = h && h.sessions.find((x) => x.id === sel.id);
  return s ? { host: h, s } : null;
}

function select(sel) {
  selected = { host: sel.host, id: sel.id };
  store.set("sketchpad-selected", JSON.stringify(selected));
  renderSessions();
}

const RECENT_KEY = "sketchpad-recent";

function loadList(key) {
  try { return JSON.parse(store.get(key)) || []; } catch { return []; }
}

function remember(sel, label) {
  const rest = loadList(RECENT_KEY).filter((r) => !(r.host === sel.host && r.id === sel.id));
  store.set(RECENT_KEY, JSON.stringify([{ host: sel.host, id: sel.id, label }, ...rest].slice(0, 4)));
}

function renderRecent() {
  const box = $("recent");
  box.innerHTML = "";
  for (const r of loadList(RECENT_KEY)) {
    if (selected && r.host === selected.host && r.id === selected.id) continue;
    const found = findSession(r);
    if (!found || !found.s.via) continue;
    const b = document.createElement("button");
    b.textContent = found.s.label || r.label;
    b.title = r.host;
    b.onclick = () => select(r);
    box.append(b);
    if (box.children.length === 3) break;
  }
  box.hidden = !box.children.length;
}

const HISTORY_KEY = "sketchpad-history";

async function thumbnail(dataURL) {
  const img = new Image();
  img.src = dataURL;
  await img.decode();
  const s = Math.min(1, 160 / img.naturalWidth, 120 / img.naturalHeight);
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(img.naturalWidth * s));
  c.height = Math.max(1, Math.round(img.naturalHeight * s));
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL("image/jpeg", 0.7);
}

function addHistory(entry, image) {
  store.set(HISTORY_KEY, JSON.stringify([{ ...entry, thumb: null }, ...loadList(HISTORY_KEY)].slice(0, 20)));
  if (!image) return;
  thumbnail(image).then((thumb) => {
    const list = loadList(HISTORY_KEY);
    const found = list.find((e) => e.at === entry.at && e.host === entry.host && e.id === entry.id && e.image === entry.image);
    if (found) {
      found.thumb = thumb;
      store.set(HISTORY_KEY, JSON.stringify(list));
    }
  }).catch(() => {});
}

function openHistory() {
  const ul = $("history-list");
  ul.innerHTML = "";
  const list = loadList(HISTORY_KEY);
  $("history-status").textContent = list.length ? "" : "Nothing sent from this device yet.";
  for (const e of list) {
    const li = document.createElement("li");
    if (e.thumb) li.append(Object.assign(new Image(), { src: e.thumb }));
    const meta = document.createElement("div");
    meta.innerHTML = `<div class="name"></div><div class="sub"></div>`;
    meta.querySelector(".name").textContent = e.text || "(sketch only)";
    meta.querySelector(".sub").textContent = `${e.label} · ${e.host} · ${new Date(e.at).toLocaleString()}`;
    li.append(meta);
    li.onclick = () => reopen(e);
    ul.append(li);
  }
  $("history").hidden = false;
}

let reopening = false;
async function reopen(e) {
  if (reopening) return;
  reopening = true;
  try {
    if (e.image) await insertImage(await api(`/api/image?${new URLSearchParams({ host: e.host, path: e.image })}`));
    $("text").value = e.text || "";
    if (findSession(e)) select(e);
    $("history").hidden = true;
  } catch (err) {
    $("history-status").textContent = err.message;
  } finally {
    reopening = false;
  }
}

$("open-history").onclick = openHistory;
$("history-close").onclick = () => { $("history").hidden = true; };

function updateSendButton() {
  const found = findSession(selected);
  const name = found && (found.s.label || found.s.id);
  $("send").textContent = found ? `Send to ${name}` : "Send";
  $("send").title = found ? `${found.host.host} / ${name}` : "";
}

function renderSessions() {
  const ul = $("sessions");
  ul.innerHTML = "";
  const note = (message) => {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = message;
    ul.append(li);
  };
  for (const h of hosts) {
    const head = document.createElement("li");
    head.className = "hosthead";
    head.textContent = h.online ? h.host : `${h.host} · offline`;
    ul.append(head);
    if (!h.online) note(h.error ? `Can't reach ${h.host}: ${h.error}` : `Can't reach ${h.host}. Is its helper (and tunnel) running?`);
    else if (!h.sessions.length) note("No Claude Code sessions running.");
    for (const s of h.sessions) {
      const li = document.createElement("li");
      if (selected && selected.host === h.host && selected.id === s.id) li.classList.add("selected");
      if (!s.via) li.classList.add("disabled");
      const dir = (s.cwd || "").split("/").pop();
      li.innerHTML = `<div class="name"><span class="dot"></span></div><div class="sub"></div>`;
      li.querySelector(".dot").classList.toggle("busy", s.status !== "idle");
      li.querySelector(".name").append(s.label || s.id);
      li.querySelector(".sub").textContent = s.via ? `${dir} · ${s.status}` : "not reachable";
      li.onclick = () => { if (s.via) select({ host: h.host, id: s.id }); };
      ul.append(li);
    }
  }
  updateSendButton();
  renderRecent();
}

function refreshSessions() {
  api("/api/sessions")
    .then((data) => { hosts = data.hosts; renderSessions(); })
    .catch((e) => setStatus(e.message, true));
}
refreshSessions();
setInterval(refreshSessions, 3000);

$("logout").onclick = () =>
  fetch("/api/logout", { method: "POST" }).then(() => location.replace("/login.html"));

// ---- resizable split ----
const sidebar = $("sidebar");
const savedWidth = store.get("sketchpad-sidebar");
if (savedWidth) sidebar.style.width = savedWidth;

$("divider").addEventListener("pointerdown", (e) => {
  const div = e.currentTarget;
  div.setPointerCapture(e.pointerId);
  const move = (ev) => {
    const pct = Math.min(50, Math.max(5, (ev.clientX / window.innerWidth) * 100));
    sidebar.style.width = pct + "%";
  };
  const up = () => {
    div.removeEventListener("pointermove", move);
    store.set("sketchpad-sidebar", sidebar.style.width);
    if (excalidraw) excalidraw.refresh(); // Excalidraw caches its offsets; recompute after the resize
  };
  div.addEventListener("pointermove", move);
  div.addEventListener("pointerup", up, { once: true });
});

// ---- board ----
let excalidraw = null; // imperative API, set once mounted

createRoot($("board")).render(React.createElement(Excalidraw, {
  excalidrawAPI: (a) => {
    excalidraw = a;
    window.sketchpad = { api: a, exportPng, insertImage,
      get MAX_SEND() { return sendLimit.MAX_SEND; }, set MAX_SEND(v) { sendLimit.MAX_SEND = v; } }; // e2e hook
  },
  initialData: { appState: { viewBackgroundColor: "#ffffff" } },
  UIOptions: { canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false } },
}));

const EXPORT_MAX = 4096;

async function exportPng() {
  const elements = excalidraw.getSceneElements();
  if (!elements.length) return null;
  const blob = await exportToBlob({
    elements,
    appState: { ...excalidraw.getAppState(), exportBackground: true, viewBackgroundColor: "#ffffff" },
    files: excalidraw.getFiles(),
    mimeType: "image/png",
    getDimensions: (w, h) => {
      const scale = Math.min(2, EXPORT_MAX / Math.max(w, h));
      return { width: Math.round(w * scale), height: Math.round(h * scale), scale };
    },
  });
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(blob);
  });
}

// ---- open an image (e.g. a plot) from one of the machines onto the board ----
const MAX_W = 1600, MAX_H = 1200;
const MAX_SRC_W = MAX_W * 2, MAX_SRC_H = MAX_H * 2;

function shrink(img, dataURL, mimeType) {
  const w = img.naturalWidth, h = img.naturalHeight;
  const s = Math.min(1, MAX_SRC_W / w, MAX_SRC_H / h);
  if (s === 1 || mimeType === "image/svg+xml") return { dataURL, mimeType };
  const c = document.createElement("canvas");
  c.width = Math.round(w * s);
  c.height = Math.round(h * s);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  const out = mimeType === "image/jpeg" ? "image/jpeg" : "image/png";
  return { dataURL: c.toDataURL(out, 0.9), mimeType: out };
}

async function insertImage({ name, mimeType, dataURL }) {
  const img = new Image();
  img.src = dataURL;
  await img.decode();
  ({ dataURL, mimeType } = shrink(img, dataURL, mimeType));
  // An SVG without width/height reports 0; give it a sane default size.
  const w = img.naturalWidth || 800, h = img.naturalHeight || 600;
  const scale = Math.min(1, MAX_W / w, MAX_H / h);
  const existing = excalidraw.getSceneElements();
  const right = existing.reduce((m, e) => Math.max(m, e.x + e.width), 0);
  const fileId = `${Date.now()}-${name}`;
  excalidraw.addFiles([{ id: fileId, dataURL, mimeType, created: Date.now() }]);
  // Locked so pen strokes on top of the plot don't grab and move it.
  const [element] = convertToExcalidrawElements([{
    type: "image", fileId, locked: true,
    x: existing.length ? right + 40 : 0, y: 0, width: w * scale, height: h * scale,
  }]);
  // Recorded in history so an accidental insert can be undone (it's locked, so hard to delete).
  excalidraw.updateScene({
    elements: [...excalidraw.getSceneElementsIncludingDeleted(), element],
    captureUpdate: CaptureUpdateAction.IMMEDIATELY,
  });
  excalidraw.scrollToContent(element, { fitToContent: true });
}

function formatSize(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`;
}

function ago(mtime) {
  const s = Date.now() / 1000 - mtime;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function browserStatus(msg) { $("browser-status").textContent = msg; }
let browseSeq = 0;
let picking = false;

async function browse(path) {
  const seq = ++browseSeq;
  const host = $("browser-host").value;
  browserStatus("Loading…");
  try {
    const d = await api(`/api/ls?${new URLSearchParams({ host, path })}`);
    if (seq !== browseSeq) return;
    $("browser-path").value = d.path;
    const ul = $("browser-list");
    ul.innerHTML = "";
    const add = (label, cls, onclick) => {
      const li = document.createElement("li");
      li.textContent = label;
      li.className = cls;
      li.onclick = onclick;
      ul.append(li);
    };
    if (d.parent) add("../", "dir", () => browse(d.parent));
    const base = d.path.endsWith("/") ? d.path : d.path + "/";
    for (const e of d.entries) {
      if (e.dir) add(`${e.name}/`, "dir", () => browse(base + e.name));
      else add(`${e.name} · ${formatSize(e.size)} · ${ago(e.mtime)}`, "img", () => pick(base + e.name));
    }
    browserStatus(d.entries.length ? "" : "No folders or images here.");
  } catch (e) {
    if (seq === browseSeq) browserStatus(e.message);
  }
}

async function pick(path) {
  if (picking) return;
  picking = true;
  ++browseSeq;
  browserStatus("Opening…");
  try {
    await insertImage(await api(`/api/image?${new URLSearchParams({ host: $("browser-host").value, path })}`));
    picking = false;
    $("browser").hidden = true;
  } catch (e) {
    browserStatus(e.message);
  } finally {
    picking = false;
  }
}

$("open-image").onclick = () => {
  const host = hosts.find((h) => selected && h.host === selected.host) || hosts[0];
  const session = host && selected && host.sessions.find((s) => s.id === selected.id);
  const picker = $("browser-host");
  picker.innerHTML = "";
  for (const h of hosts) picker.append(new Option(h.online ? h.host : `${h.host} (offline)`, h.host));
  if (host) picker.value = host.host;
  $("browser").hidden = false;
  browse(session?.cwd || "~");
};
$("browser-host").onchange = () => browse("~");
$("browser-close").onclick = () => { $("browser").hidden = true; };
$("browser-path").oninput = () => { ++browseSeq; };
$("browser-path").onkeydown = (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const path = $("browser-path").value.trim();
  if (/\.(png|jpe?g|gif|webp|svg)$/i.test(path)) pick(path);
  else browse(path);
};

// ---- send ----
// The LAN page is plain HTTP, so crypto.randomUUID is unavailable.
const makeId = () => Date.now().toString(36) + Math.random().toString(36).slice(2);
let pendingSend = null;
const sendLimit = { MAX_SEND: 24 * 1024 * 1024 };

$("send").onclick = async () => {
  const destination = selected && { host: selected.host, id: selected.id };
  const found = findSession(destination);
  if (!found) return setStatus(selected ? "That session is gone. Pick another on the left." : "Pick a session on the left first.", true);
  const text = $("text").value.trim();
  $("send").disabled = true;
  try {
    const image = await exportPng();
    if (!text && !image) return setStatus("Nothing to send.", true);
    const req = { host: destination.host, session: destination.id, text, image };
    const key = JSON.stringify(req);
    if (key.length > sendLimit.MAX_SEND)
      return setStatus(`Too big to send (${formatSize(key.length)}; limit ${formatSize(sendLimit.MAX_SEND)}). Remove or crop an image.`, true);
    if (!pendingSend || pendingSend.key !== key) pendingSend = { id: makeId(), key };
    setStatus("Sending…");
    const r = await api("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...req, send_id: pendingSend.id }),
    });
    pendingSend = null;
    const name = found.s.label || found.s.id;
    remember(destination, name);
    addHistory({ at: Date.now(), host: destination.host, id: destination.id, label: name, text, image: r.image }, image);
    setStatus(`${r.duplicate ? "Already sent to" : "Sent to"} ${name} via ${r.via === "socket" ? "inbox" : r.via} ✓ ${new Date().toLocaleTimeString()}`);
    renderRecent();
    $("text").value = "";
    excalidraw.resetScene();
  } catch (e) {
    setStatus(`Send failed: ${e.message}`, true);
  } finally {
    $("send").disabled = false;
  }
};
