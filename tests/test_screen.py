import subprocess
import unittest
from unittest import mock

import files
import screen

PNG = b"\x89PNG\r\n\x1a\n" + b"rest"


def result(stdout=b"", returncode=0, stderr=b""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class CaptureTest(unittest.TestCase):
    def capture(self, mode, **run):
        with mock.patch.object(screen.subprocess, "run", **run) as r:
            return screen.capture(mode), r

    def test_full_returns_image_like_read_image(self):
        img, run = self.capture("full", return_value=result(PNG))
        self.assertEqual(run.call_args.args[0], ["flameshot", "full", "--raw"])
        self.assertEqual(img["mimeType"], "image/png")
        self.assertTrue(img["dataURL"].startswith("data:image/png;base64,iVBORw0KGg"))

    def test_box_uses_flameshot_gui_and_waits_longer(self):
        _, run = self.capture("box", return_value=result(PNG))
        self.assertEqual(run.call_args.args[0], ["flameshot", "gui", "--raw"])
        self.assertGreater(run.call_args.kwargs["timeout"], 60)
        # On Wayland the overlay covers only one monitor; through XWayland it covers all.
        self.assertEqual(run.call_args.kwargs["env"]["QT_QPA_PLATFORM"], "xcb")

    def test_escape_is_cancelled(self):
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.capture("box", return_value=result(b""))

    def test_escape_with_error_exit_is_cancelled(self):
        # flameshot 13 on GNOME exits non-zero on Esc, with Qt noise before "Screenshot aborted."
        stderr = b"QPainter::drawEllipse: Painter not active\nflameshot: info: Screenshot aborted.\n"
        with self.assertRaisesRegex(ValueError, "^screenshot cancelled$"):
            self.capture("box", return_value=result(b"", 1, stderr))

    def test_flameshot_error_is_reported(self):
        with self.assertRaisesRegex(ValueError, "no display"):
            self.capture("full", return_value=result(b"", 1, b"qt: no display"))

    def test_not_installed(self):
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.capture("full", side_effect=FileNotFoundError("flameshot"))

    def test_timeout(self):
        with self.assertRaisesRegex(ValueError, "timed out"):
            self.capture("box", side_effect=subprocess.TimeoutExpired(["flameshot"], 120))

    def test_unknown_mode_is_400(self):
        status, body = files.call(screen.capture, "rm -rf")
        self.assertEqual(status, 400)
        self.assertIn("unknown", body["error"])


if __name__ == "__main__":
    unittest.main()
