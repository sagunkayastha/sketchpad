"""Screenshot this machine's screen with flameshot, for when you're sitting at it.

"full" grabs every monitor at once. "box" opens flameshot's own selection overlay on this
screen and waits for you to drag a box and press Enter (Esc cancels).
"""
import base64
import subprocess

COMMANDS = {"full": ["flameshot", "full", "--raw"], "box": ["flameshot", "gui", "--raw"]}
TIMEOUTS = {"full": 15, "box": 120}  # box waits for a person


def capture(mode):
    """Returns the same shape as files.read_image; raises ValueError for anything the user should see."""
    if mode not in COMMANDS:
        raise ValueError(f"unknown screenshot mode: {mode}")
    try:
        r = subprocess.run(COMMANDS[mode], capture_output=True, timeout=TIMEOUTS[mode])
    except FileNotFoundError:
        raise ValueError("flameshot is not installed on this machine") from None
    except subprocess.TimeoutExpired:
        raise ValueError("screenshot timed out") from None
    if not r.stdout.startswith(b"\x89PNG"):
        # Esc exits 0 on some setups and non-zero with "Screenshot aborted." on others (GNOME Wayland).
        if r.returncode == 0 or b"Screenshot aborted" in r.stderr:
            raise ValueError("screenshot cancelled")
        raise ValueError(f"flameshot failed: {r.stderr.decode(errors='replace').strip()[-300:]}")
    data = base64.b64encode(r.stdout).decode()
    return {"name": f"screen-{mode}.png", "mimeType": "image/png", "dataURL": f"data:image/png;base64,{data}"}
