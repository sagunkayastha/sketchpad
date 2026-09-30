"""Browser end-to-end check: real hub + headless Chrome. Run: python3 tests/e2e_ui.py"""
import os
import struct
import subprocess
import sys
import tempfile
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


def main():
    home = Path(tempfile.mkdtemp())
    auth.save_credentials(home / ".config" / "sketchpad" / "auth.json", "e2e", "e2e-pass")
    (home / "my plots").mkdir()
    make_png(home / "my plots" / "wide plot.png", 3000, 1500)
    hub = subprocess.Popen([sys.executable, "server.py", "serve", "--bind", "127.0.0.1", "--port", str(PORT)],
                           cwd=ROOT, env={**os.environ, "HOME": str(home)})
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

    page.evaluate("""() => {
      const [rect] = window.ExcalidrawLib.convertToExcalidrawElements([{type: 'rectangle', x: 0, y: 0, width: 120, height: 60}]);
      window.sketchpad.api.updateScene({elements: [rect]});
    }""")
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


if __name__ == "__main__":
    main()
