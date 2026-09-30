"""Discover running Claude Code sessions and deliver messages to them through tmux or kitty."""
import glob
import json
import os
import stat
import subprocess
import time
from pathlib import Path

SESSIONS_DIR = Path.home() / ".claude" / "sessions"


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_sessions(sessions_dir=SESSIONS_DIR):
    """Claude Code writes one <pid>.json per running session; files can outlive a crash."""
    out = []
    for f in sorted(sessions_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data.get("pid"), int) and pid_alive(data["pid"]):
            out.append(data)
    return out


def parent_map():
    ps = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, check=True)
    return {int(pid): int(ppid) for pid, ppid in (line.split() for line in ps.stdout.splitlines())}


def tmux_panes():
    """{pane shell pid: target}; empty if tmux isn't installed/running."""
    try:
        r = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_pid}\t#{pane_id}\t#{session_name}"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return {}
    panes = {}
    for line in r.stdout.splitlines():
        pid, pane_id, name = line.split("\t", 2)
        panes[int(pid)] = {"kind": "tmux", "pane": pane_id, "label": name}
    return panes


def parse_kitty_ls(text, sock):
    out = {}
    for os_window in json.loads(text):
        for tab in os_window["tabs"]:
            for w in tab["windows"]:
                target = {"kind": "kitty", "socket": sock, "window": w["id"]}
                out[w["pid"]] = target
                for fg in w.get("foreground_processes", []):
                    out[fg["pid"]] = target
    return out


def kitty_windows():
    """{window pid: target} across every kitty listening on $XDG_RUNTIME_DIR/kitty-<pid>."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    windows = {}
    for sock in glob.glob(f"{runtime}/kitty-*"):
        if not stat.S_ISSOCK(os.stat(sock).st_mode):
            continue
        try:
            r = subprocess.run(["kitty", "@", "--to", f"unix:{sock}", "ls"],
                               capture_output=True, text=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            windows.update(parse_kitty_ls(r.stdout, sock))
    return windows


def find_target(pid, ppids, targets):
    # Nearest ancestor wins: claude in tmux inside kitty goes through tmux.
    while pid and pid > 1:
        if pid in targets:
            return targets[pid]
        pid = ppids.get(pid)
    return None


def list_sessions():
    ppids = parent_map()
    targets = {**kitty_windows(), **tmux_panes()}
    result = []
    for s in read_sessions():
        target = find_target(s["pid"], ppids, targets)
        result.append({
            "id": s.get("sessionId"),
            "name": s.get("name"),
            "cwd": s.get("cwd"),
            "status": s.get("status"),
            "startedAt": s.get("startedAt"),
            "label": (target or {}).get("label") or s.get("name"),
            "via": target["kind"] if target else None,
            "target": target,
        })
    return result


def build_message(text, image_path):
    # A newline would submit the prompt early, so collapse all whitespace.
    text = " ".join((text or "").split())
    parts = [text, f"[sketch: {image_path}]" if image_path else ""]
    return " ".join(p for p in parts if p)


def deliver(target, message):
    if target["kind"] == "tmux":
        subprocess.run(["tmux", "send-keys", "-t", target["pane"], "-l", "--", message], check=True)
        time.sleep(0.3)  # let Claude Code ingest the text before submitting
        subprocess.run(["tmux", "send-keys", "-t", target["pane"], "Enter"], check=True)
    else:
        kitty = ["kitty", "@", "--to", f"unix:{target['socket']}", "send-text", "--match", f"id:{target['window']}"]
        # --stdin sends the text as-is; the positional form would interpret backslash escapes.
        subprocess.run([*kitty, "--stdin"], input=message, text=True, check=True)
        time.sleep(0.3)
        subprocess.run([*kitty, "\\r"], check=True)
