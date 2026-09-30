import base64
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import auth
import server

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class FileRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name).resolve()
        (cls.d / "p.png").write_bytes(PNG)
        for h in (server.HubHandler, server.HelperHandler):
            h.log_message = lambda *a: None
        server.HelperHandler.token = "helper-secret"
        cls.helper = start(server.HelperHandler)
        server.HubHandler.creds = {"username": "u", "secret": "cookie-secret"}
        server.HubHandler.helper_token = "helper-secret"
        server.HubHandler.remotes = {
            "remote": f"http://127.0.0.1:{cls.helper.server_address[1]}",
            "dead": "http://127.0.0.1:9",  # nothing listens on the discard port
        }
        cls.hub = start(server.HubHandler)
        cls.cookie = f"{server.COOKIE}={auth.make_cookie('cookie-secret', 'u')}"

    @classmethod
    def tearDownClass(cls):
        cls.hub.shutdown()
        cls.helper.shutdown()
        cls.tmp.cleanup()

    def get(self, srv, path, headers):
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def hub_get(self, route, **query):
        return self.get(self.hub, f"{route}?{urllib.parse.urlencode(query)}", {"Cookie": self.cookie})

    def test_local_ls_defaults_to_hub_host(self):
        status, body = self.hub_get("/api/ls", path=str(self.d))
        self.assertEqual(status, 200)
        self.assertEqual([e["name"] for e in body["entries"]], ["p.png"])

    def test_remote_image_through_helper(self):
        status, body = self.hub_get("/api/image", host="remote", path=str(self.d / "p.png"))
        self.assertEqual(status, 200)
        self.assertTrue(body["dataURL"].startswith("data:image/png;base64,"))

    def test_remote_error_passes_through(self):
        status, _ = self.hub_get("/api/image", host="remote", path=str(self.d / "missing.png"))
        self.assertEqual(status, 404)

    def test_requires_login(self):
        status, _ = self.get(self.hub, f"/api/ls?path={self.d}", {})
        self.assertEqual(status, 401)

    def test_unknown_host(self):
        self.assertEqual(self.hub_get("/api/ls", host="nope", path="/")[0], 404)

    def test_offline_host_is_502_fast(self):
        t = time.monotonic()
        status, body = self.hub_get("/api/ls", host="dead", path="/")
        self.assertEqual((status, body["error"]), (502, "dead is offline"))
        self.assertLess(time.monotonic() - t, 6)

    def test_helper_rejects_bad_token(self):
        status, _ = self.get(self.helper, f"/api/ls?path={self.d}", {"X-Helper-Token": "wrong"})
        self.assertEqual(status, 401)


class DeliverLocalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sketches = Path(self.tmp.name)
        server.RECENT_SENDS.clear()
        session = {"id": "s1", "label": "L", "via": "tmux", "target": {"kind": "tmux", "pane": "%1"}}
        self.list = mock.patch.object(server.sessions, "list_sessions", return_value=[session]).start()
        self.deliver = mock.patch.object(server.sessions, "deliver").start()
        mock.patch.object(server, "SKETCHES", self.sketches).start()
        self.addCleanup(mock.patch.stopall)

    def req(self, **kw):
        image = "data:image/png;base64," + base64.b64encode(PNG).decode()
        return {"session": "s1", "text": "hi", "image": image, "send_id": "a1", **kw}

    def test_resolves_route_fresh_and_reports_it(self):
        status, body = server.deliver_local(self.req())
        self.list.assert_called_with(fresh=True)
        self.assertEqual((status, body["via"], body["label"]), (200, "tmux", "L"))

    def test_same_send_id_delivers_once(self):
        _, first = server.deliver_local(self.req())
        status, again = server.deliver_local(self.req())
        self.assertEqual(status, 200)
        self.assertEqual(self.deliver.call_count, 1)
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["image"], first["image"])

    def test_same_id_with_changed_content_delivers_again(self):
        server.deliver_local(self.req())
        server.deliver_local(self.req(text="changed"))
        self.assertEqual(self.deliver.call_count, 2)

    def test_failure_removes_png_and_reports(self):
        self.deliver.side_effect = server.sessions.DeliveryError("tmux: boom")
        status, body = server.deliver_local(self.req())
        self.assertEqual((status, body["partial"]), (502, False))
        self.assertIn("boom", body["error"])
        self.assertEqual(list(self.sketches.iterdir()), [])

    def test_failed_send_can_be_retried(self):
        self.deliver.side_effect = [server.sessions.DeliveryError("boom"), None]
        self.assertEqual(server.deliver_local(self.req())[0], 502)
        self.assertEqual(server.deliver_local(self.req())[0], 200)
        self.assertEqual(self.deliver.call_count, 2)

    def test_partial_keeps_png(self):
        self.deliver.side_effect = server.sessions.DeliveryError("typed, not submitted", partial=True)
        status, body = server.deliver_local(self.req())
        self.assertEqual((status, body["partial"]), (502, True))
        self.assertEqual(len(list(self.sketches.iterdir())), 1)

if __name__ == "__main__":
    unittest.main()
