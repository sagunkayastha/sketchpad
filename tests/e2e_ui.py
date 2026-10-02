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
    # Fake the portal boundary and browser CLI: no real screen or URL capture.
    make_png(home / "fake-screen.png", 640, 360)
    make_png(home / "fake-url.png", 800, 450)
    (home / "bin").mkdir()
    fakes = home / "fakes"
    fakes.mkdir()
    (fakes / "sitecustomize.py").write_text('''import os, tempfile
from pathlib import Path
import portal_dbus
h = Path(os.environ["HOME"])
def fake_screenshot(interactive, timeout):
    with (h / "portal-args").open("a") as log:
        log.write(f"{interactive} {timeout}\\n")
    if (h / "cancel").exists(): return 1, None
    fd, name = tempfile.mkstemp(suffix=".png", dir=h)
    with os.fdopen(fd, "wb") as output:
        output.write((h / "fake-screen.png").read_bytes())
    return 0, Path(name).as_uri()
portal_dbus.screenshot = fake_screenshot
''')
    browser_cli = home / "bin" / "google-chrome"
    browser_cli.write_text('''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
h = Path(os.environ["HOME"])
with (h / "browser-args").open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
path = next(arg.split("=", 1)[1] for arg in sys.argv[1:] if arg.startswith("--screenshot="))
shutil.copyfile(h / "fake-url.png", path)
''')
    browser_cli.chmod(0o755)
    inbox = FakeInbox(home / "inbox.sock")
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(home), TMUX_TMPDIR=str(home),
               PATH=f"{home / 'bin'}:{os.environ['PATH']}", PYTHONPATH=f"{fakes}:{ROOT}")
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
            run_screen_checks(page, home)
            run_url_checks(page, home)
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
    check("picked image lands on board unlocked, so it can be resized and cropped",
          len(img) == 1 and img[0]["type"] == "image" and not img[0]["locked"])
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

    add_session(home, inbox, "s2", "e2e-two")
    page.locator("#sessions li", has_text="e2e-two").click(timeout=10000)
    page.fill("#text", "second")
    page.click("#send")
    page.wait_for_function("document.getElementById('status').textContent.includes('Sent to e2e-two')", timeout=10000)
    chip = page.locator("#recent button", has_text="e2e-one")
    check("recent row offers the other recent session", chip.count() == 1)
    chip.click()
    check("tapping a recent chip selects it", page.inner_text("#send") == "Send to e2e-one")

    page.click("#open-history")
    page.wait_for_selector("#history:not([hidden])")
    items = page.locator("#history-list li")
    check("history lists sends newest first", items.count() >= 2 and "second" in items.nth(0).inner_text())
    items.filter(has_text="e2e hello").click()
    page.wait_for_selector("#history", state="hidden")
    check("reopening puts the sketch back on the board and the text in the box",
          page.evaluate("window.sketchpad.api.getSceneElements().length") == 1
          and page.input_value("#text") == "e2e hello")
    check("reopening selects that session", page.inner_text("#send") == "Send to e2e-one")

    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = (...args) => original(...args).then((response) => {
        if (args[0] !== '/api/send') return response;
        return new Promise((resolve) => { window.releaseSend = () => resolve(response); });
      });
    }""")
    page.evaluate("window.sketchpad.api.resetScene()")
    page.fill("#text", "delayed A")
    page.click("#send")
    page.wait_for_function("typeof window.releaseSend === 'function'", timeout=10000)
    page.locator("#sessions li", has_text="e2e-two").click()
    page.evaluate("window.releaseSend()")
    page.wait_for_function("document.getElementById('status').textContent.includes('Sent to e2e-one')", timeout=10000)
    entry = page.evaluate("JSON.parse(localStorage.getItem('sketchpad-history'))[0]")
    check("switching sessions during send keeps History on original destination",
          entry["id"] == "s1" and entry["text"] == "delayed A")


def run_screen_checks(page, home):
    page.evaluate("window.sketchpad.api.resetScene()")
    page.click("#open-screen")
    page.wait_for_selector("#screen-menu:not([hidden])")
    check("screen menu defaults to the selected session's machine",
          page.input_value("#screen-host") == page.evaluate("JSON.parse(localStorage.getItem('sketchpad-selected')).host"))
    page.click("#shot-full")
    page.wait_for_function("window.sketchpad.api.getSceneElements().length === 1", timeout=10000)
    size = page.evaluate("""async () => {
      const f = Object.values(window.sketchpad.api.getFiles()).pop();
      const i = new Image(); i.src = f.dataURL; await i.decode(); return [i.naturalWidth, i.naturalHeight];
    }""")
    check("Full screen puts the (fake) screenshot on the board", size == [640, 360])
    check("menu is hidden after capturing", page.locator("#screen-menu").is_hidden())

    page.click("#open-screen")
    page.click("#shot-box")
    page.wait_for_function("window.sketchpad.api.getSceneElements().length === 2", timeout=10000)
    calls = (home / "portal-args").read_text().splitlines()
    check("portal full is noninteractive and box uses its area picker",
          calls == ["False 15", "True 120"])

    (home / "cancel").touch()
    page.click("#open-screen")
    page.click("#shot-box")
    page.wait_for_function("document.getElementById('status').textContent.includes('cancelled')", timeout=10000)
    check("portal cancel shows 'cancelled' and adds nothing",
          page.evaluate("window.sketchpad.api.getSceneElements().length") == 2)
    page.evaluate("window.sketchpad.api.resetScene()")

    # "This computer" uses the browser's share picker; stand in a 320x200 canvas stream for it.
    page.evaluate("""() => {
      const c = document.createElement("canvas"); c.width = 320; c.height = 200;
      const g = c.getContext("2d"); g.fillStyle = "#c00";
      setInterval(() => g.fillRect(0, 0, 320, 200), 50);  // keep frames coming in headless Chrome
      navigator.mediaDevices.getDisplayMedia = async () => (window.fakeStream = c.captureStream());
    }""")
    page.click("#open-screen")
    page.click("#shot-local")
    page.wait_for_function("window.sketchpad.api.getSceneElements().length === 1", timeout=10000)
    size = page.evaluate("""async () => {
      const f = Object.values(window.sketchpad.api.getFiles()).pop();
      const i = new Image(); i.src = f.dataURL; await i.decode(); return [i.naturalWidth, i.naturalHeight];
    }""")
    check("This computer puts the shared screen's frame on the board", size == [320, 200])
    check("sharing stops after the one frame",
          page.evaluate("window.fakeStream.getTracks().every((t) => t.readyState === 'ended')"))
    page.evaluate("() => { navigator.mediaDevices.getDisplayMedia = async () => { throw new DOMException('denied', 'NotAllowedError'); }; }")
    page.click("#open-screen")
    page.click("#shot-local")
    page.wait_for_function("document.getElementById('status').textContent.includes('cancelled')", timeout=10000)
    check("cancelling the share picker shows 'cancelled' and adds nothing",
          page.evaluate("window.sketchpad.api.getSceneElements().length") == 1)
    page.evaluate("window.sketchpad.api.resetScene()")


def run_url_checks(page, home):
    page.click("#open-url")
    page.wait_for_selector("#url-dialog:not([hidden])")
    check("URL capture targets the selected session's machine",
          page.locator("#url-host").text_content() ==
          page.evaluate("JSON.parse(localStorage.getItem('sketchpad-selected')).host"))
    page.fill("#url-input", "http://localhost:5173/plot")
    page.click("#url-capture")
    page.wait_for_function("window.sketchpad.api.getSceneElements().length === 1", timeout=10000)
    image = page.evaluate("window.sketchpad.api.getSceneElements()[0]")
    check("URL screenshot lands unlocked and scaled on board",
          image["type"] == "image" and not image["locked"] and image["width"] <= 1600)
    args = (home / "browser-args").read_text().splitlines()
    check("headless browser got localhost URL on that machine",
          len(args) == 1 and args[0].endswith("http://localhost:5173/plot"))
    page.click("#open-url")
    page.fill("#url-input", "file:///etc/passwd")
    page.click("#url-capture")
    page.wait_for_function("document.getElementById('url-status').textContent.includes('http')")
    check("invalid URL stays in dialog and adds no image",
          page.evaluate("window.sketchpad.api.getSceneElements().length") == 1)
    page.click("#url-close")
    page.evaluate("window.sketchpad.api.resetScene()")


if __name__ == "__main__":
    main()
