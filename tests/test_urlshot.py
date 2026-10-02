import subprocess
import unittest
from pathlib import Path
from unittest import mock

import urlshot

PNG = b"\x89PNG\r\n\x1a\n" + b"browser screenshot"


class URLShotTest(unittest.TestCase):
    def test_headless_browser_captures_url_without_shell(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            Path(next(a.split("=", 1)[1] for a in argv if a.startswith("--screenshot="))).write_bytes(PNG)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        with mock.patch.object(urlshot.shutil, "which", side_effect=lambda name: "/usr/bin/chromium" if name == "chromium" else None), \
             mock.patch.object(urlshot.subprocess, "run", side_effect=run):
            image = urlshot.capture("http://localhost:5173/chart?x=1&y=2")
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], "/usr/bin/chromium")
        self.assertIn("--headless=new", argv)
        self.assertIn("--window-size=1440,900", argv)
        self.assertIn("--hide-scrollbars", argv)
        profile = next(a.split("=", 1)[1] for a in argv if a.startswith("--user-data-dir="))
        screenshot = next(a.split("=", 1)[1] for a in argv if a.startswith("--screenshot="))
        self.assertEqual(Path(profile).parent, Path(screenshot).parent)
        self.assertNotEqual(Path(profile), Path.home() / ".config" / "google-chrome")
        self.assertEqual(argv[-1], "http://localhost:5173/chart?x=1&y=2")
        self.assertGreaterEqual(kwargs["timeout"], 25)
        self.assertEqual(image["mimeType"], "image/png")
        self.assertTrue(image["dataURL"].startswith("data:image/png;base64,iVBORw0KGg"))

    def test_rejects_non_http_urls(self):
        for value in ("file:///etc/passwd", "javascript:alert(1)", "localhost:5173", "https://", ""):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "http"):
                urlshot.capture(value)

    def test_missing_browser_is_clear(self):
        with mock.patch.object(urlshot.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "Chrome or Chromium"):
                urlshot.capture("https://example.com")

    def test_timeout_is_clear(self):
        with mock.patch.object(urlshot.shutil, "which", return_value="/usr/bin/chromium"), \
             mock.patch.object(urlshot.subprocess, "run", side_effect=subprocess.TimeoutExpired(["chromium"], 30)):
            with self.assertRaisesRegex(ValueError, "timed out"):
                urlshot.capture("https://example.com")

    def test_browser_failure_reports_stderr(self):
        failure = subprocess.CompletedProcess([], 1, b"", b"navigation failed")
        with mock.patch.object(urlshot.shutil, "which", return_value="/usr/bin/chromium"), \
             mock.patch.object(urlshot.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(ValueError, "navigation failed"):
                urlshot.capture("https://example.com")

    def test_success_without_image_is_an_error(self):
        success = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(urlshot.shutil, "which", return_value="/usr/bin/chromium"), \
             mock.patch.object(urlshot.subprocess, "run", return_value=success):
            with self.assertRaisesRegex(ValueError, "did not write a screenshot"):
                urlshot.capture("https://example.com")


if __name__ == "__main__":
    unittest.main()
