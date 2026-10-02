"""sketchpad server.

serve  - the hub: web page, login, this machine's sessions, and remote helpers' sessions.
helper - API-only agent for another machine; the hub reaches it through an SSH tunnel.
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import auth
import files
import screen
import sessions
import urlshot

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SKETCHES = ROOT / "sketches"
HELPER_TOKEN_FILE = Path.home() / ".config" / "sketchpad" / "helper-token"
COOKIE = "sp_session"
MAX_BODY = 25 * 1024 * 1024
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
# Routes answered by whichever machine the request is for; the hub forwards them to helpers.
# Looked up at call time, so tests that patch screen.capture can never reach the real screen.
MACHINE_ROUTES = {"/api/ls": lambda path: files.list_dir(path),
                  "/api/image": lambda path: files.read_image(path),
                  "/api/screenshot": lambda mode: screen.capture(mode),
                  "/api/urlshot": lambda url: urlshot.capture(url)}
HELPER_TIMEOUTS = {"/api/ls": 5, "/api/image": 20, "/api/screenshot": 130,
                   "/api/urlshot": 40}  # a box screenshot waits for a person


def route_arg(route, query):
    """(name, value) of the one argument a machine route takes."""
    name = {"/api/screenshot": "mode", "/api/urlshot": "url"}.get(route, "path")
    return name, query.get(name, [""])[0]


def public(s):
    return {k: v for k, v in s.items() if k != "target"}


RECENT_SENDS = {}
RECENT_TTL = 600
SEND_LOCK = threading.Lock()


def deliver_local(req):
    """Deduplicate a retried send on the machine that owns the session."""
    send_id = req.get("send_id")
    digest = hashlib.sha256(json.dumps([req.get("session"), req.get("text", ""), req.get("image")]).encode()).hexdigest()
    with SEND_LOCK:
        now = time.monotonic()
        for k in [k for k, v in RECENT_SENDS.items() if now - v[1] > RECENT_TTL]:
            del RECENT_SENDS[k]
        if send_id in RECENT_SENDS and RECENT_SENDS[send_id][0] == digest:
            status, body = RECENT_SENDS[send_id][2]
            return status, {**body, "duplicate": True}
        status, body = _deliver(req)
        # A partial terminal failure already typed text; retrying must not type it again.
        if send_id and (status == 200 or body.get("partial")):
            RECENT_SENDS[send_id] = (digest, now, (status, body))
        return status, body


def _deliver(req):
    target = next((s for s in sessions.list_sessions(fresh=True) if s["id"] == req.get("session")), None)
    if not target or not target["target"]:
        return 404, {"error": "session not found or not reachable"}
    image_path = None
    if req.get("image"):
        SKETCHES.mkdir(exist_ok=True)
        image_path = SKETCHES / f"sketch-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() // 1_000_000 % 1000:03d}.png"
        image_path.write_bytes(base64.b64decode(req["image"].split(",", 1)[-1]))
    message = sessions.build_message(req.get("text", ""), image_path)
    if not message:
        return 400, {"error": "nothing to send"}
    try:
        sessions.deliver(target["target"], message)
    except sessions.DeliveryError as e:
        if image_path and not e.partial:
            image_path.unlink(missing_ok=True)
        return 502, {"error": str(e), "partial": e.partial}
    return 200, {"ok": True, "image": str(image_path) if image_path else None,
                 "via": target["via"], "label": target["label"]}


def call_helper(url, path, token, body=None, timeout=3):
    """Returns (status, body); raises OSError if the helper is unreachable."""
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Helper-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


class BaseHandler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="application/json", headers=()):
        data = json.dumps(body).encode() if ctype == "application/json" else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            raise ValueError("too large")
        return json.loads(self.rfile.read(length) or b"{}")


class HubHandler(BaseHandler):
    creds = None
    host = socket.gethostname()
    remotes = {}  # name -> helper URL
    helper_token = ""

    def authorized(self):
        # The page can type into Claude sessions, so every API call needs a login.
        morsel = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
        if morsel and auth.verify_cookie(self.creds["secret"], morsel.value):
            return True
        self.reply(401, {"error": "login required"})
        return False

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path.startswith("/api/"):
            if not self.authorized():
                return
            if url.path == "/api/sessions":
                self.reply(200, {"hosts": self.all_hosts()})
            elif url.path in MACHINE_ROUTES:
                self.reply(*self.files_request(url.path, urllib.parse.parse_qs(url.query)))
            else:
                self.reply(404, {"error": "not found"})
            return
        f = (STATIC / ("index.html" if url.path == "/" else url.path.lstrip("/"))).resolve()
        if f.parent != STATIC or not f.is_file():
            self.reply(404, {"error": "not found"})
            return
        self.reply(200, f.read_bytes(), CONTENT_TYPES.get(f.suffix, "application/octet-stream"))

    def files_request(self, route, query):
        host = query.get("host", [self.host])[0]
        name, arg = route_arg(route, query)
        if host == self.host:
            return files.call(MACHINE_ROUTES[route], arg)
        if host not in self.remotes:
            return 404, {"error": f"unknown host {host}"}
        try:
            return call_helper(self.remotes[host], f"{route}?{urllib.parse.urlencode({name: arg})}",
                               self.helper_token, timeout=HELPER_TIMEOUTS[route])
        except (OSError, ValueError):
            return 502, {"error": f"{host} is offline"}

    def all_hosts(self):
        hosts = [{"host": self.host, "online": True,
                  "sessions": [public(s) for s in sessions.list_sessions()]}]
        for name, url in self.remotes.items():
            try:
                status, body = call_helper(url, "/api/list", self.helper_token)
            except (OSError, ValueError):
                hosts.append({"host": name, "online": False, "sessions": []})
                continue
            if status != 200:
                hosts.append({"host": name, "online": False, "error": body.get("error", f"HTTP {status}"), "sessions": []})
            else:
                hosts.append({"host": name, "online": True, "sessions": body.get("sessions", [])})
        return hosts

    def do_POST(self):
        routes = {"/api/login": self.login, "/api/logout": self.logout, "/api/send": self.send}
        route = routes.get(self.path)
        if not route:
            self.reply(404, {"error": "not found"})
            return
        if self.path != "/api/login" and not self.authorized():
            return
        try:
            req = self.read_json()
        except ValueError as e:
            self.reply(400, {"error": str(e)})
            return
        route(req)

    def login(self, req):
        if not auth.check_login(self.creds, str(req.get("username", "")), str(req.get("password", ""))):
            time.sleep(1)  # slow down guessing
            self.reply(401, {"error": "wrong username or password"})
            return
        cookie = auth.make_cookie(self.creds["secret"], self.creds["username"])
        self.reply(200, {"ok": True}, headers=[("Set-Cookie",
            f"{COOKIE}={cookie}; Path=/; Max-Age={auth.SESSION_TTL}; HttpOnly; SameSite=Strict")])

    def logout(self, req):
        self.reply(200, {"ok": True}, headers=[("Set-Cookie",
            f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")])

    def send(self, req):
        host = req.get("host")
        if host == self.host:
            self.reply(*deliver_local(req))
        elif host in self.remotes:
            try:
                self.reply(*call_helper(self.remotes[host], "/api/deliver", self.helper_token, req, timeout=20))
            except (OSError, ValueError):
                self.reply(502, {"error": f"{host} is offline"})
        else:
            self.reply(404, {"error": f"unknown host {host}"})


class HelperHandler(BaseHandler):
    token = ""

    def authorized(self):
        if hmac.compare_digest(self.headers.get("X-Helper-Token", ""), self.token):
            return True
        self.reply(401, {"error": "bad helper token"})
        return False

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/api/list" and url.path not in MACHINE_ROUTES:
            self.reply(404, {"error": "not found"})
        elif not self.authorized():
            return
        elif url.path == "/api/list":
            self.reply(200, {"sessions": [public(s) for s in sessions.list_sessions()]})
        else:
            _, arg = route_arg(url.path, urllib.parse.parse_qs(url.query))
            self.reply(*files.call(MACHINE_ROUTES[url.path], arg))

    def do_POST(self):
        if self.path != "/api/deliver":
            self.reply(404, {"error": "not found"})
            return
        if not self.authorized():
            return
        try:
            req = self.read_json()
        except ValueError as e:
            self.reply(400, {"error": str(e)})
            return
        self.reply(*deliver_local(req))


def set_password():
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    if not username or not password or password != getpass.getpass("Repeat password: "):
        sys.exit("Empty username/password, or the passwords don't match. Nothing saved.")
    auth.save_credentials(auth.AUTH_FILE, username, password)
    print(f"Saved to {auth.AUTH_FILE}. Restart the server; all devices must log in again.")


def load_helper_token():
    try:
        token = HELPER_TOKEN_FILE.read_text().strip()
    except FileNotFoundError:
        sys.exit(f"Missing {HELPER_TOKEN_FILE} (shared secret between hub and helper).")
    if not token:
        sys.exit(f"{HELPER_TOKEN_FILE} is empty. Create it with: openssl rand -hex 32 > {HELPER_TOKEN_FILE}")
    return token


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", choices=["serve", "helper", "set-password"], default="serve")
    ap.add_argument("--bind", action="append", help="address to listen on (repeatable)")
    ap.add_argument("--port", type=int)
    ap.add_argument("--remote", action="append", default=[], metavar="NAME=URL",
                    help="helper to include, e.g. laptop=http://127.0.0.1:8791 (repeatable)")
    args = ap.parse_args()
    if args.command == "set-password":
        set_password()
        return
    if args.command == "helper":
        HelperHandler.token = load_helper_token()
        handler, port = HelperHandler, args.port or 8791
    else:
        HubHandler.creds = auth.load_credentials()
        if not HubHandler.creds:
            sys.exit("No login set. Run: python3 server.py set-password")
        HubHandler.remotes = dict(r.split("=", 1) for r in args.remote)
        if HubHandler.remotes:
            HubHandler.helper_token = load_helper_token()
        handler, port = HubHandler, args.port or 8790
    servers = [ThreadingHTTPServer((addr, port), handler) for addr in args.bind or ["127.0.0.1"]]
    for srv in servers:
        print(f"sketchpad {args.command} on http://{srv.server_address[0]}:{port}/", flush=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
