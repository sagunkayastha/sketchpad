import tempfile
import unittest
from pathlib import Path

import auth


class CredentialsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "auth.json"
        auth.save_credentials(self.path, "alice", "hunter22")
        self.creds = auth.load_credentials(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_is_private_and_has_no_plaintext(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("hunter22", self.path.read_text())

    def test_check_login(self):
        self.assertTrue(auth.check_login(self.creds, "alice", "hunter22"))
        self.assertFalse(auth.check_login(self.creds, "alice", "wrong"))
        self.assertFalse(auth.check_login(self.creds, "other", "hunter22"))

    def test_changing_password_rotates_secret(self):
        auth.save_credentials(self.path, "alice", "new-pass")
        self.assertNotEqual(auth.load_credentials(self.path)["secret"], self.creds["secret"])

    def test_missing_file(self):
        self.assertIsNone(auth.load_credentials(Path(self.tmp.name) / "nope.json"))


class CookieTest(unittest.TestCase):
    def test_roundtrip(self):
        c = auth.make_cookie("s3cret", "alice", now=1000)
        self.assertEqual(auth.verify_cookie("s3cret", c, now=1001), "alice")

    def test_expired(self):
        c = auth.make_cookie("s3cret", "alice", now=1000)
        self.assertIsNone(auth.verify_cookie("s3cret", c, now=1000 + auth.SESSION_TTL + 1))

    def test_wrong_secret_or_tampered(self):
        c = auth.make_cookie("s3cret", "alice", now=1000)
        self.assertIsNone(auth.verify_cookie("other", c, now=1001))
        self.assertIsNone(auth.verify_cookie("s3cret", c[:-1] + ("0" if c[-1] != "0" else "1"), now=1001))
        self.assertIsNone(auth.verify_cookie("s3cret", "garbage", now=1001))


if __name__ == "__main__":
    unittest.main()
