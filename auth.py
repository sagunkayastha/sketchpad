"""Username/password login: scrypt-hashed credentials and stateless signed session cookies."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

AUTH_FILE = Path.home() / ".config" / "sketchpad" / "auth.json"
SESSION_TTL = 30 * 24 * 60 * 60


def _hash(password, salt_hex):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1).hex()


def save_credentials(path, username, password):
    # A fresh secret on every save, so changing the password logs out every device.
    salt = secrets.token_hex(16)
    data = {"username": username, "salt": salt, "hash": _hash(password, salt),
            "secret": secrets.token_hex(32)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(data))


def load_credentials(path=AUTH_FILE):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def check_login(creds, username, password):
    user_ok = hmac.compare_digest(username.encode(), creds["username"].encode())
    pass_ok = hmac.compare_digest(_hash(password, creds["salt"]), creds["hash"])
    return user_ok and pass_ok


def _sign(secret, payload):
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_cookie(secret, username, now=None):
    expires = int((now or time.time()) + SESSION_TTL)
    payload = base64.urlsafe_b64encode(f"{expires}|{username}".encode()).decode()
    return f"{payload}.{_sign(secret, payload)}"


def verify_cookie(secret, value, now=None):
    """Return the username for a valid, unexpired cookie, else None."""
    payload, _, sig = value.rpartition(".")
    if not payload or not hmac.compare_digest(sig, _sign(secret, payload)):
        return None
    expires, _, username = base64.urlsafe_b64decode(payload).decode().partition("|")
    return username if int(expires) > (now or time.time()) else None
