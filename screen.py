"""Capture this machine's screen.

Modes: "box" (drag a region), "screen" (the monitor under the cursor), "all" (every monitor
in one image; "full" is the old name), or "screen:NAME" (one monitor, from list_screens()).

KDE uses spectacle's command line: no dialog, and box selection starts at once. Windows uses
screen_win. Anything else falls back to the desktop Screenshot portal (box and all only).
"""
import base64
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import portal_dbus

TIMEOUTS = {"all": 15, "screen": 15, "box": 120}  # box waits for a person
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def parse_mode(mode):
    """(kind, monitor name or None); raises ValueError for anything else."""
    if mode == "full":
        mode = "all"
    if mode in TIMEOUTS:
        return mode, None
    if mode.startswith("screen:") and mode[7:]:
        return "screen", mode[7:]
    raise ValueError(f"unknown screenshot mode: {mode}")


def backend():
    if sys.platform == "win32":
        return "windows"
    if shutil.which("spectacle"):
        return "spectacle"
    return "portal"


def image(data, name, crop=None):
    if not data.startswith(PNG_MAGIC):
        raise ValueError("screenshot is not a PNG")
    shot = {"name": name, "mimeType": "image/png",
            "dataURL": "data:image/png;base64," + base64.b64encode(data).decode()}
    if crop:
        shot["crop"] = crop  # the browser cuts this rectangle out before inserting
    return shot


def list_screens():
    """{"screens": [{name, x, y, width, height, primary}], "cursor": bool}.

    Positions are desktop (logical) coordinates; "cursor" says whether "screen" (the monitor
    under the cursor) works here.
    """
    kind = backend()
    if kind == "windows":
        import screen_win
        return {"screens": screen_win.monitors(), "cursor": True}
    if kind == "spectacle":
        return {"screens": kde_screens(), "cursor": True}
    return {"screens": [], "cursor": False}  # the portal can't tell monitors apart


def capture(mode):
    """Same shape as files.read_image; raises ValueError for user-facing errors."""
    kind, name = parse_mode(mode)
    label = f"screen-{name or kind}.png"
    which = backend()
    if which == "windows":
        import screen_win
        return image(screen_win.capture(kind, name), label)
    if which == "spectacle":
        return spectacle_capture(kind, name, label)
    if kind == "screen":
        raise ValueError("picking one screen needs KDE (spectacle) or Windows")
    return portal_capture(kind, label)


# ---- KDE ----

ROTATED = {2, 8, 32, 128}  # kscreen rotation: left, right and their flipped twins are portrait


def kde_screens(run=subprocess.run):
    try:
        out = run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=10)
        outputs = json.loads(out.stdout)["outputs"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        raise ValueError(f"could not list screens: {error}") from error
    screens = []
    for o in outputs:
        if not (o.get("enabled") and o.get("connected")):
            continue
        # The current mode is the panel's own (landscape) size; "size" may already be turned.
        mode = next((m for m in o.get("modes", []) if m.get("id") == o.get("currentModeId")), None)
        w, h = (mode or o)["size"]["width"], (mode or o)["size"]["height"]
        if o.get("rotation") in ROTATED:
            w, h = h, w
        scale = o.get("scale") or 1
        screens.append({"name": o["name"], "x": o["pos"]["x"], "y": o["pos"]["y"],
                        "width": round(w / scale), "height": round(h / scale),
                        "primary": o.get("priority") == 1})
    return sorted(screens, key=lambda s: (s["x"], s["y"]))


def crop_for(screens, name):
    """The named monitor as a rectangle relative to the whole desktop, plus the desktop's size."""
    match = next((s for s in screens if s["name"] == name), None)
    if not match:
        raise ValueError(f"no screen named {name}")
    left = min(s["x"] for s in screens)
    top = min(s["y"] for s in screens)
    return {"x": match["x"] - left, "y": match["y"] - top, "width": match["width"],
            "height": match["height"],
            "desktopWidth": max(s["x"] + s["width"] for s in screens) - left,
            "desktopHeight": max(s["y"] + s["height"] for s in screens) - top}


def spectacle_args(kind, name):
    if kind == "box":
        return ["-r"]
    if kind == "screen" and name is None:
        return ["-m"]
    return ["-f"]  # all, or one named monitor cut out of the whole desktop


def spectacle_capture(kind, name, label, run=subprocess.run):
    crop = crop_for(kde_screens(run), name) if name else None
    with tempfile.TemporaryDirectory(prefix="sketchpad-shot-") as tmp:
        path = Path(tmp) / f"{secrets.token_hex(6)}.png"
        try:
            done = run(["spectacle", "-b", "-n", *spectacle_args(kind, name), "-o", str(path)],
                       capture_output=True, timeout=TIMEOUTS[kind])
        except subprocess.TimeoutExpired as error:
            raise ValueError("screenshot timed out") from error
        if not path.exists():
            if kind == "box" and done.returncode == 0:
                raise ValueError("screenshot cancelled")
            said = (done.stderr or b"").decode(errors="replace").strip().splitlines()
            raise ValueError("spectacle saved no image (is a Spectacle window open? close it)"
                             + (f": {said[-1]}" if said else ""))
        return image(path.read_bytes(), label, crop)


# ---- desktop portal (non-KDE Linux) ----

def request(interactive, timeout):
    return portal_dbus.screenshot(interactive, timeout)


def portal_capture(kind, label):
    code, uri_text = request(kind == "box", TIMEOUTS[kind])
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
    return image(data, label)
