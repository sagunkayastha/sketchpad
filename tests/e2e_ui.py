"""Browser end-to-end check: real hub + headless Chrome. Run: python3 tests/e2e_ui.py"""
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import auth  # noqa: E402

PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"


def make_png(path, w, h):
    raw = b"".join(b"\x00" + b"\x80\x80\xff" * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


class FakeInbox:
    """Receive test sends without reaching a developer's terminal."""

    def __init__(self, path):
        self.path, self.messages = str(path), []
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(self.path)
        self.srv.listen(8)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            conn, _ = self.srv.accept()
            with conn:
                buf = b""
                while buf.count(b"\n") < 2:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            lines = [json.loads(line) for line in buf.decode().splitlines() if line]
            if len(lines) == 2:
                self.messages.append(lines[1]["message"]["content"])


def add_session(home, inbox, sid, name):
    d = home / ".claude" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    start = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    (d / f"{sid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "name": name, "cwd": str(home), "status": "idle",
        "procStart": start, "messagingSocketPath": inbox.path}))
    digest = hashlib.sha256(os.path.abspath(inbox.path).encode()).hexdigest()
    (d / f"{pid}.{digest}.key").write_text(json.dumps({"peerToken": "e2e-token"}))


def draw_rect(page):
    page.evaluate("""() => {
      const [rect] = window.ExcalidrawLib.convertToExcalidrawElements([{type: 'rectangle', x: 0, y: 0, width: 120, height: 60}]);
      window.sketchpad.api.updateScene({elements: [rect]});
    }""")


def main():
    home = Path(tempfile.mkdtemp())
    auth.save_credentials(home / ".config" / "sketchpad" / "auth.json", "e2e", "e2e-pass")
    (home / "my plots").mkdir()
    make_png(home / "my plots" / "wide plot.png", 3000, 1500)
    make_png(home / "my plots" / "huge plot.png", 5000, 2500)
    inbox = FakeInbox(home / "inbox.sock")
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(home), TMUX_TMPDIR=str(home))
    hub = subprocess.Popen([sys.executable, "server.py", "serve", "--bind", "127.0.0.1", "--port", str(PORT)],
                           cwd=ROOT, env=env)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"{BASE}/login.html", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            run_checks(page, home)
            run_session_checks(page, home, inbox)
            check("no page errors", not errors or print(errors))
            browser.close()
    finally:
        hub.terminate()


def run_checks(page, home):
    page.goto(BASE + "/")
    page.wait_for_url("**/login.html")
    check("login button keeps its styling", page.evaluate("getComputedStyle(document.getElementById('submit')).borderTopLeftRadius") == "8px")
    page.fill("#username", "e2e")
    page.fill("#password", "e2e-pass")
    page.click("#submit")
    page.wait_for_function("window.sketchpad && window.sketchpad.api", timeout=60000)
    check("board mounted", page.locator(".excalidraw").count() == 1)
    check("Excalidraw inputs stay editable on iOS (user-select: text)", page.evaluate("""() => {
      const i = document.createElement('input'); document.querySelector('.excalidraw').append(i);
      const v = getComputedStyle(i).userSelect; i.remove(); return v;
    }""") == "text")

    check("empty board exports nothing", page.evaluate("window.sketchpad.exportPng()") is None)

    page.click("#text")
    page.keyboard.type("r")
    check("typing in message box keeps selection tool",
          page.evaluate("window.sketchpad.api.getAppState().activeTool.type") == "selection")
    page.fill("#text", "")

    draw_rect(page)
    png = page.evaluate("window.sketchpad.exportPng()")
    check("drawing exports a PNG", isinstance(png, str) and png.startswith("data:image/png;base64,"))
    page.evaluate("window.sketchpad.api.resetScene()")

    page.click("#open-image")
    page.wait_for_selector("#browser:not([hidden])")
    page.locator("#browser-list li", has_text="my plots/").click()
    page.locator("#browser-list li", has_text="wide plot.png").click()
    page.wait_for_selector("#browser", state="hidden")
    img = page.evaluate("window.sketchpad.api.getSceneElements().map(e => ({type: e.type, locked: e.locked, x: e.x, w: e.width, h: e.height}))")
    check("picked image lands on board, locked", len(img) == 1 and img[0]["type"] == "image" and img[0]["locked"])
    check("3000x1500 plot scaled to fit 1600x1200", abs(img[0]["w"] - 1600) < 1 and abs(img[0]["h"] - 800) < 1)
    png = page.evaluate("window.sketchpad.exportPng()")
    check("image-only board still exports", isinstance(png, str) and png.startswith("data:image/png;base64,"))

    page.click('button[aria-label="Undo"]')
    check("undo removes an inserted image", page.evaluate("window.sketchpad.api.getSceneElements().length") == 0)
    page.click('button[aria-label="Redo"]')
    check("redo brings it back", page.evaluate("window.sketchpad.api.getSceneElements().length") == 1)

    page.click("#open-image")
    page.fill("#browser-path", "~/my plots/wide plot.png")
    page.press("#browser-path", "Enter")
    page.wait_for_selector("#browser", state="hidden")
    two = page.evaluate("window.sketchpad.api.getSceneElements().map(e => e.x)")
    check("path box opens image with ~ and spaces, placed to the right", len(two) == 2 and two[1] >= two[0] + 1600 + 40 - 1)

    page.click("#open-image")
    page.fill("#browser-path", "~/nope")
    page.press("#browser-path", "Enter")
    page.wait_for_function("document.getElementById('browser-status').textContent.includes('not found')")
    check("missing folder shows an error in the dialog", True)
    page.click("#browser-close")

    page.evaluate("window.sketchpad.api.resetScene()")
    page.click("#open-image")
    page.locator("#browser-list li", has_text="my plots/").click()
    page.locator("#browser-list li", has_text="wide plot.png").dblclick()
    page.wait_for_selector("#browser", state="hidden")
    page.wait_for_timeout(500)
    check("double-tap inserts the image once", page.evaluate("window.sketchpad.api.getSceneElements().length") == 1)
    page.evaluate("window.sketchpad.api.resetScene()")

    draw_rect(page)
    width = page.evaluate("""async () => {
      const i = new Image(); i.src = await window.sketchpad.exportPng(); await i.decode(); return i.naturalWidth;
    }""")
    check("small sketch exports at 2x (120px rect -> >=270px PNG)", width >= 270)
    page.evaluate("window.sketchpad.api.resetScene()")
    page.evaluate("""() => {
      const [rect] = window.ExcalidrawLib.convertToExcalidrawElements([{type: 'rectangle', x: 0, y: 0, width: 5000, height: 100}]);
      window.sketchpad.api.updateScene({elements: [rect]});
    }""")
    width = page.evaluate("""async () => {
      const i = new Image(); i.src = await window.sketchpad.exportPng(); await i.decode(); return i.naturalWidth;
    }""")
    check("wide board export stays within 4096px", width <= 4096)
    page.evaluate("window.sketchpad.api.resetScene()")

    page.click("#open-image")
    page.fill("#browser-path", "~/my plots/huge plot.png")
    page.press("#browser-path", "Enter")
    page.wait_for_selector("#browser", state="hidden")
    dims = page.evaluate("""async () => {
      const files = window.sketchpad.api.getFiles();
      const e = window.sketchpad.api.getSceneElements()[0];
      const i = new Image(); i.src = files[e.fileId].dataURL; await i.decode(); return [i.naturalWidth, i.naturalHeight];
    }""")
    check("5000x2500 source stored at <=3200x2400", dims[0] <= 3200 and dims[1] <= 2400)
    page.evaluate("window.sketchpad.api.resetScene()")


def run_session_checks(page, home, inbox):
    page.wait_for_selector("#sessions li.empty")
    check("empty state explains there are no sessions", "No Claude Code sessions running." in page.inner_text("#sessions"))

    add_session(home, inbox, "s1", "e2e-one")
    row = page.locator("#sessions li", has_text="e2e-one")
    row.wait_for(timeout=10000)
    check("fake session goes through the inbox, never a real terminal", "not reachable" not in row.inner_text())
    check("session rows are touch-sized", row.bounding_box()["height"] >= 44)
    row.click()
    check("Send names the destination", page.inner_text("#send") == "Send to e2e-one")
    check("Send keeps the accent button styling", page.evaluate("""() => {
      const s = getComputedStyle(document.getElementById('send'));
      return s.backgroundColor === 'rgb(79, 140, 255)' && s.color === 'rgb(255, 255, 255)';
    }"""))

    draw_rect(page)
    page.fill("#text", "e2e hello")
    page.click("#send")
    page.wait_for_function("document.getElementById('status').textContent.includes('Sent to e2e-one')", timeout=10000)
    check("session received the text and the sketch path",
          len(inbox.messages) == 1 and inbox.messages[0].startswith("e2e hello [sketch: "))

    (home / ".claude" / "sessions" / "s1.json").unlink()
    page.wait_for_function("document.getElementById('send').textContent === 'Send'", timeout=10000)
    page.fill("#text", "to nowhere")
    page.click("#send")
    check("closed session: Send says it's gone instead of posting",
          "gone" in page.inner_text("#status") and len(inbox.messages) == 1)
    page.fill("#text", "")
    add_session(home, inbox, "s1", "e2e-one")
    page.wait_for_function("document.getElementById('send').textContent === 'Send to e2e-one'", timeout=10000)

    page.evaluate("window.sketchpad.MAX_SEND = 100")
    draw_rect(page)
    page.click("#send")
    page.wait_for_function("document.getElementById('status').textContent.includes('Too big to send')", timeout=10000)
    check("oversize send refused before upload", "Too big to send" in page.inner_text("#status"))
    page.evaluate("window.sketchpad.MAX_SEND = 24 * 1024 * 1024")
    page.evaluate("window.sketchpad.api.resetScene()")


if __name__ == "__main__":
    main()
