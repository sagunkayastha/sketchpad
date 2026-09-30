import hashlib
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import sessions


class FindTargetTest(unittest.TestCase):
    def test_walks_up_to_pane_shell(self):
        ppids = {300: 200, 200: 100, 100: 1}
        targets = {100: {"kind": "tmux", "pane": "%3"}}
        self.assertEqual(sessions.find_target(300, ppids, targets), {"kind": "tmux", "pane": "%3"})

    def test_nearest_ancestor_wins(self):
        # claude (300) in a tmux pane (200) whose tmux client runs inside a kitty window (100)
        ppids = {300: 200, 200: 100, 100: 1}
        targets = {200: {"kind": "tmux"}, 100: {"kind": "kitty"}}
        self.assertEqual(sessions.find_target(300, ppids, targets)["kind"], "tmux")

    def test_none_when_unreachable(self):
        ppids = {300: 200, 200: 1}
        self.assertIsNone(sessions.find_target(300, ppids, {100: {"kind": "tmux"}}))


class SocketTargetTest(unittest.TestCase):
    def test_reads_peer_token_from_key_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            sock = "/run/user/1000/cc-socks/42.sock"
            digest = hashlib.sha256(sock.encode()).hexdigest()
            (d / f"42.{digest}.key").write_text(json.dumps({"peerToken": "ab" * 16}))
            target = sessions.socket_target({"pid": 42, "messagingSocketPath": sock}, d)
        self.assertEqual(target, {"kind": "socket", "socket": sock, "token": "ab" * 16, "label": None})

    def test_none_without_socket_or_key(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sessions.socket_target({"pid": 42}, Path(d)))
            self.assertIsNone(sessions.socket_target({"pid": 42, "messagingSocketPath": "/x.sock"}, Path(d)))


class DeliverSocketTest(unittest.TestCase):
    def test_sends_auth_then_user_message(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.sock")
            srv = socket.socket(socket.AF_UNIX)
            srv.bind(path)
            srv.listen(1)
            got = []

            def accept():
                conn, _ = srv.accept()
                with conn:
                    buf = b""
                    while b"\n" not in buf or buf.count(b"\n") < 2:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    got.append(buf)

            t = threading.Thread(target=accept, daemon=True)
            t.start()
            sessions.deliver({"kind": "socket", "socket": path, "token": "t0k"}, "hi [sketch: /a.png]")
            t.join(5)
            srv.close()
        lines = [json.loads(l) for l in got[0].decode().splitlines()]
        self.assertEqual(lines[0], {"type": "auth", "token": "t0k"})
        self.assertEqual(lines[1], {"type": "user", "message": {"role": "user", "content": "hi [sketch: /a.png]"}})


class ParseKittyLsTest(unittest.TestCase):
    def test_maps_shell_and_foreground_pids(self):
        ls = json.dumps([{"tabs": [{"windows": [
            {"id": 7, "pid": 500, "foreground_processes": [{"pid": 501}]},
        ]}]}])
        out = sessions.parse_kitty_ls(ls, "/run/user/1000/kitty-42")
        want = {"kind": "kitty", "socket": "/run/user/1000/kitty-42", "window": 7}
        self.assertEqual(out, {500: want, 501: want})


class BuildMessageTest(unittest.TestCase):
    def test_text_and_image(self):
        self.assertEqual(
            sessions.build_message("look\nat  this", "/s/a.png"),
            "look at this [sketch: /s/a.png]",
        )

    def test_text_only(self):
        self.assertEqual(sessions.build_message("hi", None), "hi")

    def test_image_only(self):
        self.assertEqual(sessions.build_message("  ", "/s/a.png"), "[sketch: /s/a.png]")


class ReadSessionsTest(unittest.TestCase):
    def test_skips_dead_pids_and_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "alive"}))
            (d / "2.json").write_text(json.dumps({"pid": 2**22 + 7, "sessionId": "dead"}))
            (d / "3.json").write_text("{not json")
            ids = [s["sessionId"] for s in sessions.read_sessions(d)]
        self.assertEqual(ids, ["alive"])

    def test_live_pid_with_wrong_start_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "recycled", "procStart": "1"}))
            self.assertEqual(sessions.read_sessions(Path(d)), [])


class ProcStartTest(unittest.TestCase):
    def fake_proc(self, d, pid, start):
        rest = ["S"] + [str(i) for i in range(4, 53)]
        rest[19] = start
        (d / str(pid)).mkdir()
        (d / str(pid) / "stat").write_text(f"{pid} (a b) c) " + " ".join(rest) + "\n")

    def test_reads_field_22_even_with_parens_in_name(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.fake_proc(d, 7, "123")
            self.assertEqual(sessions.proc_start(7, d), "123")

    def test_missing_pid_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sessions.proc_start(7, Path(d)))

    def test_same_process_compares_start_time(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.fake_proc(d, 7, "123")
            self.assertTrue(sessions.same_process({"pid": 7, "procStart": "123"}, d))
            self.assertFalse(sessions.same_process({"pid": 7, "procStart": "999"}, d))

    def test_falls_back_to_pid_check(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sessions, "pid_alive", return_value=True) as alive:
            d = Path(d)
            self.fake_proc(d, 7, "123")
            self.assertTrue(sessions.same_process({"pid": 7}, d))
            self.assertTrue(sessions.same_process({"pid": 8, "procStart": "1"}, d))
            self.assertEqual(alive.call_count, 2)


class RouteCacheTest(unittest.TestCase):
    def setUp(self):
        sessions._route_cache.clear()
        self.ps = mock.patch.object(sessions, "parent_map", return_value={}).start()
        self.kitty = mock.patch.object(sessions, "kitty_windows", return_value={}).start()
        self.tmux = mock.patch.object(sessions, "tmux_panes", return_value={}).start()
        mock.patch.object(sessions, "read_sessions", return_value=[{"pid": 42, "sessionId": "s"}]).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(sessions._route_cache.clear)

    def calls(self):
        return (self.ps.call_count, self.kitty.call_count, self.tmux.call_count)

    def test_listing_reuses_routes(self):
        sessions.list_sessions()
        sessions.list_sessions()
        self.assertEqual(self.calls(), (1, 1, 1))

    def test_fresh_rediscovers(self):
        sessions.list_sessions()
        sessions.list_sessions(fresh=True)
        self.assertEqual(self.calls(), (2, 2, 2))

    def test_routes_expire(self):
        sessions.list_sessions()
        later = time.monotonic() + sessions.ROUTE_TTL + 1
        with mock.patch.object(sessions.time, "monotonic", return_value=later):
            sessions.list_sessions()
        self.assertEqual(self.calls(), (2, 2, 2))


class DeliverErrorsTest(unittest.TestCase):
    def test_missing_socket_is_delivery_error(self):
        with self.assertRaises(sessions.DeliveryError) as cm:
            sessions.deliver({"kind": "socket", "socket": "/nonexistent/x.sock", "token": "t"}, "hi")
        self.assertFalse(cm.exception.partial)

    def run_deliver(self, target, side_effect):
        with mock.patch.object(sessions.subprocess, "run", side_effect=side_effect), \
             mock.patch.object(sessions.time, "sleep"), \
             self.assertRaises(sessions.DeliveryError) as cm:
            sessions.deliver(target, "hi")
        return cm.exception

    def test_typing_failure_is_not_partial(self):
        err = subprocess.CalledProcessError(1, ["tmux"], stderr="can't find pane: %1")
        e = self.run_deliver({"kind": "tmux", "pane": "%1"}, err)
        self.assertFalse(e.partial)
        self.assertIn("can't find pane", str(e))

    def test_tmux_enter_failure_is_partial(self):
        err = subprocess.CalledProcessError(1, ["tmux"])
        self.assertTrue(self.run_deliver({"kind": "tmux", "pane": "%1"}, [mock.Mock(), err]).partial)

    def test_kitty_enter_failure_is_partial(self):
        err = subprocess.CalledProcessError(1, ["kitty"])
        target = {"kind": "kitty", "socket": "/s", "window": 3}
        self.assertTrue(self.run_deliver(target, [mock.Mock(), err]).partial)

    def test_hung_typing_times_out(self):
        # A stuck terminal must not hold the send lock forever.
        err = subprocess.TimeoutExpired(["tmux"], 5)
        e = self.run_deliver({"kind": "tmux", "pane": "%1"}, err)
        self.assertFalse(e.partial)
        self.assertIn("timed out", str(e))

    def test_hung_enter_times_out_as_partial(self):
        err = subprocess.TimeoutExpired(["kitty"], 5)
        target = {"kind": "kitty", "socket": "/s", "window": 3}
        self.assertTrue(self.run_deliver(target, [mock.Mock(), err]).partial)

    def test_commands_have_a_timeout(self):
        with mock.patch.object(sessions.subprocess, "run") as run, mock.patch.object(sessions.time, "sleep"):
            sessions.deliver({"kind": "tmux", "pane": "%1"}, "hi")
        self.assertTrue(all(c.kwargs.get("timeout") for c in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
