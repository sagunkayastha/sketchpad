"""Discover running Claude Code sessions and deliver messages to them.

Delivery goes through the terminal wrapper (tmux pane or kitty window) when there is one, so the
message lands as if typed. Otherwise it goes through Claude Code's own per-session inbox socket,
which works in any terminal (VS Code, Alacritty, plain ssh) but shows up as a peer message.
"""
import glob
import hashlib
import json
import os
import socket
import stat
import subprocess
import threading
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


def proc_start(pid, proc=Path("/proc")):
    """Read process start time from field 22 of /proc/<pid>/stat."""
    try:
        text = (proc / str(pid) / "stat").read_text()
    except OSError:
        return None
    # The command in field 2 may contain spaces and closing parentheses.
    try:
        return text.rsplit(")", 1)[1].split()[19]
    except IndexError:
        return None


def same_process(session, proc=Path("/proc")):
    start = proc_start(session["pid"], proc)
    if start is None or "procStart" not in session:
        return pid_alive(session["pid"])
    return start == str(session["procStart"])


def read_sessions(sessions_dir=SESSIONS_DIR):
    """Claude Code writes one <pid>.json per running session; files can outlive a crash."""
    out = []
    for f in sorted(sessions_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data.get("pid"), int) and same_process(data):
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
                               capture_output=True, text=True, timeout=2)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            windows.update(parse_kitty_ls(r.stdout, sock))
    return windows


def socket_target(session, sessions_dir=SESSIONS_DIR):
    """Claude Code's inbox: the socket path from the session file plus the peer token from its key file."""
    sock = session.get("messagingSocketPath")
    if not sock:
        return None
    # Key file name is <pid>.<sha256 of the normalized socket path>.key, next to the session file.
    digest = hashlib.sha256(os.path.abspath(sock).encode()).hexdigest()
    try:
        token = json.loads((sessions_dir / f"{session['pid']}.{digest}.key").read_text())["peerToken"]
    except (OSError, ValueError, KeyError):
        return None
    return {"kind": "socket", "socket": sock, "token": token, "label": None}


def find_target(pid, ppids, targets):
    # Nearest ancestor wins: claude in tmux inside kitty goes through tmux.
    while pid and pid > 1:
        if pid in targets:
            return targets[pid]
        pid = ppids.get(pid)
    return None


ROUTE_TTL = 30
_route_cache = {}
_route_lock = threading.Lock()


def routing(fresh=False):
    """Cache expensive process and terminal discovery between page polls."""
    with _route_lock:
        if fresh or time.monotonic() - _route_cache.get("at", float("-inf")) > ROUTE_TTL:
            ppids = parent_map()
            targets = {**kitty_windows(), **tmux_panes()}
            _route_cache.update(at=time.monotonic(), ppids=ppids, targets=targets)
        return _route_cache["ppids"], _route_cache["targets"]


def list_sessions(fresh=False):
    ppids, targets = routing(fresh)
    result = []
    for s in read_sessions():
        target = find_target(s["pid"], ppids, targets) or socket_target(s)
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


class DeliveryError(Exception):
    """partial=True means text was typed but Enter could not be pressed."""

    def __init__(self, msg, partial=False):
        super().__init__(msg)
        self.partial = partial


def _why(e):
    return (getattr(e, "stderr", None) or "").strip() or str(e)


def deliver(target, message):
    """Deliver a message; the inbox socket provides no acknowledgement."""
    kind = target["kind"]
    if kind == "socket":
        lines = [{"type": "auth", "token": target["token"]},
                 {"type": "user", "message": {"role": "user", "content": message}}]
        try:
            with socket.socket(socket.AF_UNIX) as c:
                c.settimeout(5)
                c.connect(target["socket"])
                c.sendall("".join(json.dumps(line) + "\n" for line in lines).encode())
        except OSError as e:
            raise DeliveryError(f"inbox socket: {e}") from e
        return
    if kind == "tmux":
        type_text = ["tmux", "send-keys", "-t", target["pane"], "-l", "--", message], None
        submit = ["tmux", "send-keys", "-t", target["pane"], "Enter"], None
    else:
        kitty = ["kitty", "@", "--to", f"unix:{target['socket']}", "send-text", "--match", f"id:{target['window']}"]
        type_text = [*kitty, "--stdin"], message
        submit = [*kitty, "\\r"], None
    def run(cmd, stdin):
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=True)
    try:
        run(*type_text)
    except (OSError, subprocess.CalledProcessError) as e:
        raise DeliveryError(f"{kind}: {_why(e)}") from e
    time.sleep(0.3)
    try:
        run(*submit)
    except (OSError, subprocess.CalledProcessError) as e:
        raise DeliveryError(f"{kind}: typed the message but couldn't press Enter; press Enter in that terminal",
                            partial=True) from e
