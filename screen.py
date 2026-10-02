"""Capture this machine's screen with the desktop Screenshot portal."""
import base64
from pathlib import Path
from urllib.parse import unquote, urlsplit

import portal_dbus

TIMEOUTS = {"full": 15, "box": 120}  # box waits for a person


def request(interactive, timeout):
    return portal_dbus.screenshot(interactive, timeout)


def capture(mode):
    """Returns the same shape as files.read_image; raises ValueError for user-facing errors."""
    if mode not in TIMEOUTS:
        raise ValueError(f"unknown screenshot mode: {mode}")
    code, uri_text = request(mode == "box", TIMEOUTS[mode])
    if code == 1:
        raise ValueError("screenshot cancelled")
    if code != 0:
        raise ValueError(f"screenshot failed: portal response {code}")
    if not uri_text:
        raise ValueError("screenshot portal returned no image URI")
    uri = urlsplit(uri_text)
    if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
        raise ValueError("screenshot portal returned a non-file URI")
    path = Path(unquote(uri.path))
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read screenshot: {error}") from error
    finally:
        path.unlink(missing_ok=True)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("screenshot portal did not return a PNG")
    encoded = base64.b64encode(data).decode()
    return {"name": f"screen-{mode}.png", "mimeType": "image/png",
            "dataURL": f"data:image/png;base64,{encoded}"}
