import tempfile
import unittest
from pathlib import Path
from unittest import mock

import files
import screen

PNG = b"\x89PNG\r\n\x1a\n" + b"rest"


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name) / "portal shot.png"
        self.image.write_bytes(PNG)

    def capture(self, mode, code=0, uri=None):
        with mock.patch.object(screen, "request", return_value=(code, uri or self.image.as_uri())) as call:
            return screen.capture(mode), call

    def test_full_calls_portal_noninteractive_and_deletes_temp_png(self):
        image, call = self.capture("full")
        call.assert_called_once_with(False, 15)
        self.assertEqual(image["name"], "screen-full.png")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertTrue(image["dataURL"].startswith("data:image/png;base64,iVBORw0KGg"))
        self.assertFalse(self.image.exists())

    def test_box_calls_portal_interactive_and_waits_longer(self):
        _, call = self.capture("box")
        call.assert_called_once_with(True, 120)

    def test_response_one_is_cancelled(self):
        with self.assertRaisesRegex(ValueError, "^screenshot cancelled$"):
            self.capture("box", code=1)

    def test_other_response_code_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "portal response 2"):
            self.capture("full", code=2)

    def test_non_png_is_rejected_and_temp_file_is_deleted(self):
        self.image.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "PNG"):
            self.capture("full")
        self.assertFalse(self.image.exists())

    def test_unknown_mode_is_400(self):
        status, body = files.call(screen.capture, "rm -rf")
        self.assertEqual(status, 400)
        self.assertIn("unknown screenshot mode", body["error"])


if __name__ == "__main__":
    unittest.main()
