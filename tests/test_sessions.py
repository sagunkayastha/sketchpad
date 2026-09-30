import json
import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
