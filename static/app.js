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

function renderSessions() {
  const ul = $("sessions");
  ul.innerHTML = "";
  for (const h of hosts) {
    const head = document.createElement("li");
    head.className = "hosthead";
    head.textContent = h.online ? h.host : `${h.host} · offline`;
    ul.append(head);
    for (const s of h.sessions) {
      const li = document.createElement("li");
      if (selected && selected.host === h.host && selected.id === s.id) li.classList.add("selected");
      if (!s.via) li.classList.add("disabled");
      const dir = (s.cwd || "").split("/").pop();
      li.innerHTML = `<div class="name"><span class="dot"></span></div><div class="sub"></div>`;
      li.querySelector(".dot").classList.toggle("busy", s.status !== "idle");
      li.querySelector(".name").append(s.label || s.id);
      li.querySelector(".sub").textContent = s.via ? `${dir} · ${s.status}` : "not reachable";
      li.onclick = () => {
        if (!s.via) return;
        selected = { host: h.host, id: s.id };
        store.set("sketchpad-selected", JSON.stringify(selected));
        renderSessions();
      };
      ul.append(li);
    }
  }
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
    window.sketchpad = { api: a, exportPng, insertImage }; // hook for tests/e2e_ui.py
  },
  initialData: { appState: { viewBackgroundColor: "#ffffff" } },
  UIOptions: { canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false } },
}));

async function exportPng() {
  const elements = excalidraw.getSceneElements();
  if (!elements.length) return null;
  const blob = await exportToBlob({
    elements,
    appState: { ...excalidraw.getAppState(), exportBackground: true, viewBackgroundColor: "#ffffff" },
    files: excalidraw.getFiles(),
    mimeType: "image/png",
  });
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(blob);
  });
}

// ---- open an image (e.g. a plot) from one of the machines onto the board ----
const MAX_W = 1600, MAX_H = 1200;

async function insertImage({ name, mimeType, dataURL }) {
  const img = new Image();
  img.src = dataURL;
  await img.decode();
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

async function browse(path) {
  const host = $("browser-host").value;
  browserStatus("Loading…");
  try {
    const d = await api(`/api/ls?${new URLSearchParams({ host, path })}`);
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
    browserStatus(e.message);
  }
}

async function pick(path) {
  browserStatus("Opening…");
  try {
    await insertImage(await api(`/api/image?${new URLSearchParams({ host: $("browser-host").value, path })}`));
    $("browser").hidden = true;
  } catch (e) {
    browserStatus(e.message);
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
$("browser-path").onkeydown = (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const path = $("browser-path").value.trim();
  if (/\.(png|jpe?g|gif|webp|svg)$/i.test(path)) pick(path);
  else browse(path);
};

// ---- send ----
$("send").onclick = async () => {
  if (!selected) return setStatus("Pick a session on the left first.", true);
  const text = $("text").value.trim();
  $("send").disabled = true;
  try {
    const image = await exportPng();
    if (!text && !image) return setStatus("Nothing to send.", true);
    setStatus("Sending…");
    await api("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host: selected.host, session: selected.id, text, image }),
    });
    setStatus(`Sent ✓ ${new Date().toLocaleTimeString()}`);
    $("text").value = "";
    excalidraw.resetScene();
  } catch (e) {
    setStatus(`Send failed: ${e.message}`, true);
  } finally {
    $("send").disabled = false;
  }
};
