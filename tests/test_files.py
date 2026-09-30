import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import files

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class ListDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name).resolve()
        (self.d / "b dir").mkdir()
        (self.d / "A").mkdir()
        (self.d / ".hidden").mkdir()
        (self.d / "old plot.png").write_bytes(PNG)
        os.utime(self.d / "old plot.png", (1000, 1000))
        (self.d / "new.JPG").write_bytes(PNG)
        (self.d / "notes.txt").write_text("not an image")
        (self.d / "broken.png").symlink_to(self.d / "missing.png")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dirs_then_images_newest_first(self):
        names = [e["name"] for e in files.list_dir(str(self.d))["entries"]]
        self.assertEqual(names, ["A", "b dir", "new.JPG", "old plot.png"])

    def test_path_and_parent(self):
        out = files.list_dir(str(self.d / "A"))
        self.assertEqual(out["path"], str(self.d / "A"))
        self.assertEqual(out["parent"], str(self.d))
        self.assertIsNone(files.list_dir("/")["parent"])

    def test_tilde_and_spaces(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.d)}):
            self.assertEqual(files.list_dir("~/b dir")["path"], str(self.d / "b dir"))
            self.assertEqual(files.read_image("~/old plot.png")["name"], "old plot.png")

    def test_errors_map_to_status(self):
        self.assertEqual(files.call(files.list_dir, "relative/path")[0], 400)
        self.assertEqual(files.call(files.list_dir, str(self.d / "nope"))[0], 404)
        self.assertEqual(files.call(files.list_dir, str(self.d / "new.JPG"))[0], 400)


class ReadImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name).resolve()
        (self.d / "p.png").write_bytes(PNG)
        (self.d / "secret.env").write_text("KEY=1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_data_url(self):
        out = files.read_image(str(self.d / "p.png"))
        self.assertEqual(out["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(out["dataURL"].split(",", 1)[1]), PNG)

    def test_non_image_refused(self):
        status, body = files.call(files.read_image, str(self.d / "secret.env"))
        self.assertEqual(status, 400)
        self.assertNotIn("KEY", str(body))

    def test_too_large(self):
        with mock.patch.object(files, "MAX_IMAGE", 10):
            status, body = files.call(files.read_image, str(self.d / "p.png"))
        self.assertEqual(status, 400)
        self.assertIn("larger", body["error"])

    def test_missing(self):
        self.assertEqual(files.call(files.read_image, str(self.d / "gone.png"))[0], 404)


if __name__ == "__main__":
    unittest.main()
