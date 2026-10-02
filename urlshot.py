"""Capture a URL with a headless browser on this machine."""
import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

BROWSERS = ("google-chrome", "chromium", "chromium-browser")
TIMEOUT = 30


def capture(url):
    """Return a PNG data URL, resolving localhost on this machine."""
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid or any(ord(ch) < 32 for ch in url):
        raise ValueError("URL must start with http:// or https:// and include a host")
    browser = next((path for name in BROWSERS if (path := shutil.which(name))), None)
    if browser is None:
        raise ValueError("Chrome or Chromium is not installed on this machine")
    with tempfile.TemporaryDirectory(prefix="sketchpad-urlshot-") as folder:
        path = Path(folder) / "screenshot.png"
        profile = Path(folder) / "profile"
        profile.mkdir()
        command = [browser, "--headless=new", f"--screenshot={path}", "--window-size=1440,900",
                   "--hide-scrollbars", f"--user-data-dir={profile}", "--no-first-run",
                   "--no-default-browser-check", url]
        try:
            result = subprocess.run(command, capture_output=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ValueError("URL screenshot timed out") from None
        except OSError as error:
            raise ValueError(f"could not start browser: {error}") from error
        if result.returncode:
            why = result.stderr.decode(errors="replace").strip()[-300:]
            raise ValueError(f"browser screenshot failed: {why or f'exit {result.returncode}'}")
        try:
            data = path.read_bytes()
        except OSError:
            raise ValueError("browser did not write a screenshot") from None
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("browser did not write a PNG screenshot")
    return {"name": "url-screenshot.png", "mimeType": "image/png",
            "dataURL": "data:image/png;base64," + base64.b64encode(data).decode()}
