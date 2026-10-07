import json
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

import files
import screen
import screen_win

PNG = b"\x89PNG\r\n\x1a\n" + b"rest"


def kscreen(*outputs):
    return json.dumps({"outputs": list(outputs)})


def output(name, x, y, w, h, scale=1, rotation=1, enabled=True, priority=2):
    return {"name": name, "enabled": enabled, "connected": True, "pos": {"x": x, "y": y},
            "size": {"width": w, "height": h}, "scale": scale, "rotation": rotation,
            "priority": priority}


class FakeRun:
    """Stands in for subprocess.run: answers kscreen-doctor, and 'saves' spectacle's PNG."""

    def __init__(self, screens="", saves=PNG):
        self.screens, self.saves, self.calls = screens, saves, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == "kscreen-doctor":
            return subprocess.CompletedProcess(argv, 0, self.screens, "")
        if self.saves is not None:
            Path(argv[argv.index("-o") + 1]).write_bytes(self.saves)
        return subprocess.CompletedProcess(argv, 0, b"", b"")


class ModeTest(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(screen.parse_mode("box"), ("box", None))
        self.assertEqual(screen.parse_mode("screen"), ("screen", None))
        self.assertEqual(screen.parse_mode("all"), ("all", None))
        self.assertEqual(screen.parse_mode("full"), ("all", None))  # old name still works
        self.assertEqual(screen.parse_mode("screen:DP-2"), ("screen", "DP-2"))

    def test_unknown_mode_is_400(self):
        for mode in ("rm -rf", "screen:", ""):
            status, body = files.call(screen.capture, mode)
            self.assertEqual(status, 400)
            self.assertIn("unknown screenshot mode", body["error"])


class KdeTest(unittest.TestCase):
    TWO = kscreen(output("DP-2", 2560, 0, 3840, 2160, scale=1.5),
                  output("HDMI-A-1", 0, 0, 2560, 1440, priority=1),
                  output("OFF", 0, 0, 800, 600, enabled=False))

    def test_screens_are_logical_sorted_and_skip_disabled(self):
        self.assertEqual(screen.kde_screens(FakeRun(self.TWO)), [
            {"name": "HDMI-A-1", "x": 0, "y": 0, "width": 2560, "height": 1440, "primary": True},
            {"name": "DP-2", "x": 2560, "y": 0, "width": 2560, "height": 1440, "primary": False}])

    def test_rotated_screen_swaps_width_and_height(self):
        run = FakeRun(kscreen(output("DP-1", 0, 0, 1920, 1080, rotation=2)))
        self.assertEqual(screen.kde_screens(run)[0]["width"], 1080)

    def test_current_mode_size_wins_over_an_already_turned_size(self):
        turned = output("DP-1", 0, 0, 1080, 1920, rotation=8)  # "size" already portrait
        turned.update(currentModeId="3", modes=[{"id": "3", "size": {"width": 1920, "height": 1080}}])
        flipped = output("DP-3", 0, 0, 1920, 1080, rotation=32)  # flipped 90
        widths = [s["width"] for s in screen.kde_screens(FakeRun(kscreen(turned, flipped)))]
        self.assertEqual(widths, [1080, 1080])

    def test_spectacle_failure_says_why(self):
        run = FakeRun(saves=None)
        run_failing = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"x\nalready running")
        with self.assertRaisesRegex(ValueError, "saved no image.*already running"):
            screen.spectacle_capture("all", None, "x.png", run_failing)
        with self.assertRaisesRegex(ValueError, "saved no image"):
            screen.spectacle_capture("all", None, "x.png", run)

    def test_bad_kscreen_output_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "could not list screens"):
            screen.kde_screens(FakeRun("not json"))

    def test_crop_for_named_screen(self):
        crop = screen.crop_for(screen.kde_screens(FakeRun(self.TWO)), "DP-2")
        self.assertEqual(crop, {"x": 2560, "y": 0, "width": 2560, "height": 1440,
                                "desktopWidth": 5120, "desktopHeight": 1440})
        with self.assertRaisesRegex(ValueError, "no screen named nope"):
            screen.crop_for([], "nope")

    def test_spectacle_args_per_mode(self):
        for kind, name, args in (("box", None, "-r"), ("screen", None, "-m"),
                                 ("all", None, "-f"), ("screen", "DP-2", "-f")):
            run = FakeRun(self.TWO)
            shot = screen.spectacle_capture(kind, name, "x.png", run)
            argv = run.calls[-1]
            self.assertEqual(argv[:3], ["spectacle", "-b", "-n"])
            self.assertEqual(argv[3], args)
            self.assertTrue(shot["dataURL"].startswith("data:image/png;base64,iVBORw0KGg"))
            self.assertEqual("crop" in shot, name is not None)
            self.assertFalse(Path(argv[-1]).exists())  # temp dir cleaned up

    def test_box_without_file_is_cancelled(self):
        with self.assertRaisesRegex(ValueError, "^screenshot cancelled$"):
            screen.spectacle_capture("box", None, "x.png", FakeRun(saves=None))

    def test_non_png_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "PNG"):
            screen.spectacle_capture("all", None, "x.png", FakeRun(saves=b"bad"))

    def test_capture_uses_spectacle_when_present(self):
        with mock.patch.object(screen, "backend", return_value="spectacle"), \
             mock.patch.object(screen, "spectacle_capture", return_value="shot") as cap:
            self.assertEqual(screen.capture("screen:DP-2"), "shot")
        cap.assert_called_once_with("screen", "DP-2", "screen-DP-2.png")


class PortalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name) / "portal shot.png"
        self.image.write_bytes(PNG)
        patcher = mock.patch.object(screen, "backend", return_value="portal")
        patcher.start()
        self.addCleanup(patcher.stop)

    def capture(self, mode, code=0, uri=None):
        with mock.patch.object(screen, "request", return_value=(code, uri or self.image.as_uri())) as call:
            return screen.capture(mode), call

    def test_all_calls_portal_noninteractive_and_deletes_temp_png(self):
        image, call = self.capture("full")
        call.assert_called_once_with(False, 15)
        self.assertEqual(image["name"], "screen-all.png")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertFalse(self.image.exists())

    def test_box_calls_portal_interactive_and_waits_longer(self):
        _, call = self.capture("box")
        call.assert_called_once_with(True, 120)

    def test_one_screen_needs_kde_or_windows(self):
        with self.assertRaisesRegex(ValueError, "needs KDE"):
            screen.capture("screen")

    def test_response_one_is_cancelled(self):
        with self.assertRaisesRegex(ValueError, "^screenshot cancelled$"):
            self.capture("box", code=1)

    def test_other_response_code_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "portal response 2"):
            self.capture("all", code=2)

    def test_non_png_is_rejected_and_temp_file_is_deleted(self):
        self.image.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "PNG"):
            self.capture("all")
        self.assertFalse(self.image.exists())

    def test_portal_lists_no_screens(self):
        self.assertEqual(screen.list_screens(), {"screens": [], "cursor": False})


def decode_png(data):
    """(width, height, rgb) from a PNG made by screen_win.png (filter 0 rows only)."""
    width, height = struct.unpack(">II", data[16:24])
    idat = data.index(b"IDAT")
    size = struct.unpack(">I", data[idat - 4:idat])[0]
    raw = zlib.decompress(data[idat + 4:idat + 4 + size])
    rows = [raw[y * (width * 3 + 1):(y + 1) * (width * 3 + 1)] for y in range(height)]
    assert all(r[0] == 0 for r in rows)
    return width, height, b"".join(r[1:] for r in rows)


class WindowsHelpersTest(unittest.TestCase):
    RED, GREEN, BLUE, WHITE = b"\xff\x00\x00", b"\x00\xff\x00", b"\x00\x00\xff", b"\xff\xff\xff"

    def test_bgrx_to_rgb_drops_alpha(self):
        self.assertEqual(screen_win.bgrx_to_rgb(b"\x00\x00\xff\x00\xff\x00\x00\x99"), self.RED + self.BLUE)

    def test_png_round_trip(self):
        rgb = self.RED + self.GREEN + self.BLUE + self.WHITE
        data = screen_win.png(2, 2, rgb)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(decode_png(data), (2, 2, rgb))

    def test_dib_24bpp_bottom_up_with_row_padding(self):
        header = struct.pack("<IiiHHIIiiII", 40, 1, 2, 1, 24, 0, 0, 0, 0, 0, 0)
        # bottom row first; each 3-byte row is padded to 4 bytes
        dib = header + b"\x00\xff\x00" + b"\x00" + b"\x00\x00\xff" + b"\x00"
        self.assertEqual(decode_png(screen_win.dib_to_png(dib)), (1, 2, self.RED + self.GREEN))

    def test_dib_32bpp_bitfields_top_down(self):
        header = struct.pack("<IiiHHIIiiII", 40, 2, -1, 1, 32, 3, 0, 0, 0, 0, 0)
        masks = struct.pack("<III", 0xFF0000, 0xFF00, 0xFF)
        dib = header + masks + b"\xff\x00\x00\x00" + b"\xff\xff\xff\x00"
        self.assertEqual(decode_png(screen_win.dib_to_png(dib)), (2, 1, self.BLUE + self.WHITE))

    def test_bad_dibs_are_errors(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            screen_win.dib_to_png(b"short")
        header = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 8, 0, 0, 0, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            screen_win.dib_to_png(header + b"\x00" * 4)


if __name__ == "__main__":
    unittest.main()
