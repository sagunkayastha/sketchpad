"""Windows screen capture for the sketchpad helper, stdlib only (ctypes + zlib).

Monitors are captured with GDI in physical pixels (the process is made per-monitor DPI aware,
so a 150% display isn't captured blurry or cut short). Box selection is Windows' own snipping
overlay (ms-screenclip:, the Win+Shift+S one): it spans every monitor and puts the result on
the clipboard, which this module reads back.

The PNG and DIB helpers are plain functions so they can be tested on any OS.
"""
import struct
import sys
import time
import zlib

BOX_TIMEOUT = 120  # the snipping overlay waits for a person; Esc only shows up as this timeout


# ---- pure helpers ----

def png(width, height, rgb):
    """Encode packed 8-bit RGB rows (top row first) as a PNG."""
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def bgrx_to_rgb(pixels):
    """32-bit BGRX/BGRA pixels to packed RGB; GDI's alpha byte is meaningless, so it's dropped."""
    rgb = bytearray(len(pixels) // 4 * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = pixels[2::4], pixels[1::4], pixels[0::4]
    return bytes(rgb)


def dib_to_png(dib):
    """A CF_DIB clipboard block (BITMAPINFOHEADER + pixels, 24 or 32 bpp) as a PNG."""
    if len(dib) < 40:
        raise ValueError("clipboard image is truncated")
    size, width, height, _, bpp, compression = struct.unpack_from("<IiiHHI", dib)
    if bpp not in (24, 32) or compression not in (0, 3):  # BI_RGB, BI_BITFIELDS
        raise ValueError(f"unsupported clipboard image ({bpp} bpp, compression {compression})")
    offset = size + (12 if compression == 3 and size == 40 else 0)  # masks follow a v1 header
    rows, step = abs(height), bpp // 8
    stride = (width * bpp + 31) // 32 * 4
    if len(dib) < offset + stride * rows:
        raise ValueError("clipboard image is truncated")
    order = range(rows - 1, -1, -1) if height > 0 else range(rows)  # positive height = bottom-up
    out = bytearray()
    for y in order:
        row = dib[offset + y * stride: offset + y * stride + width * step]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::step], row[1::step], row[0::step]
        out += rgb
    return png(width, rows, bytes(out))


# ---- Windows API ----

if sys.platform == "win32":
    import ctypes as C
    import os
    from ctypes import wintypes as W

    user32, gdi32, kernel32 = C.WinDLL("user32"), C.WinDLL("gdi32"), C.WinDLL("kernel32")
    try:
        user32.SetProcessDpiAwarenessContext(C.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except AttributeError:
        user32.SetProcessDPIAware()

    class MONITORINFOEXW(C.Structure):
        _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT),
                    ("dwFlags", W.DWORD), ("szDevice", W.WCHAR * 32)]

    class BITMAPINFOHEADER(C.Structure):
        _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG),
                    ("biPlanes", W.WORD), ("biBitCount", W.WORD), ("biCompression", W.DWORD),
                    ("biSizeImage", W.DWORD), ("biXPelsPerMeter", W.LONG),
                    ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]

    MONITORENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, C.POINTER(W.RECT), W.LPARAM)
    user32.EnumDisplayMonitors.argtypes = [W.HDC, C.c_void_p, MONITORENUMPROC, W.LPARAM]
    user32.GetMonitorInfoW.argtypes = [W.HMONITOR, C.POINTER(MONITORINFOEXW)]
    user32.MonitorFromPoint.argtypes = [W.POINT, W.DWORD]
    user32.MonitorFromPoint.restype = W.HMONITOR
    user32.GetDC.argtypes = [W.HWND]
    user32.GetDC.restype = W.HDC
    user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
    gdi32.CreateCompatibleDC.argtypes = [W.HDC]
    gdi32.CreateCompatibleDC.restype = W.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]
    gdi32.CreateCompatibleBitmap.restype = W.HBITMAP
    gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
    gdi32.SelectObject.restype = W.HGDIOBJ
    gdi32.BitBlt.argtypes = [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD]
    gdi32.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p,
                                C.POINTER(BITMAPINFOHEADER), W.UINT]
    gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [W.HDC]
    user32.OpenClipboard.argtypes = [W.HWND]
    user32.GetClipboardData.argtypes = [W.UINT]
    user32.GetClipboardData.restype = W.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [W.UINT]
    user32.GetClipboardSequenceNumber.restype = W.DWORD
    kernel32.GlobalLock.argtypes = [W.HGLOBAL]
    kernel32.GlobalLock.restype = C.c_void_p
    kernel32.GlobalUnlock.argtypes = [W.HGLOBAL]
    kernel32.GlobalSize.argtypes = [W.HGLOBAL]
    kernel32.GlobalSize.restype = C.c_size_t

    CF_DIB = 8
    SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000


def _info(handle):
    info = MONITORINFOEXW()
    info.cbSize = C.sizeof(info)
    if not user32.GetMonitorInfoW(handle, C.byref(info)):
        raise ValueError("could not read monitor info")
    r = info.rcMonitor
    return {"name": info.szDevice.removeprefix("\\\\.\\"), "x": r.left, "y": r.top,
            "width": r.right - r.left, "height": r.bottom - r.top, "primary": bool(info.dwFlags & 1)}


def monitors():
    found = []
    def each(handle, _dc, _rect, _data):
        found.append(_info(handle))
        return True
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(each), 0)
    return sorted(found, key=lambda s: (s["x"], s["y"]))


def _grab(x, y, width, height):
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    try:
        old = gdi32.SelectObject(memory, bitmap)
        if not gdi32.BitBlt(memory, 0, 0, width, height, screen, x, y, SRCCOPY | CAPTUREBLT):
            raise ValueError("screen capture failed (BitBlt)")
        gdi32.SelectObject(memory, old)
        header = BITMAPINFOHEADER(biSize=C.sizeof(BITMAPINFOHEADER), biWidth=width,
                                  biHeight=-height, biPlanes=1, biBitCount=32)  # top-down
        pixels = C.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(memory, bitmap, 0, height, pixels, C.byref(header), 0) != height:
            raise ValueError("screen capture failed (GetDIBits)")
        return png(width, height, bgrx_to_rgb(pixels.raw))
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)


def _clipboard_dib():
    for _ in range(10):  # the snipping tool may still hold the clipboard
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise ValueError("clipboard is busy")
    try:
        handle = user32.GetClipboardData(CF_DIB)
        if not handle:
            raise ValueError("clipboard has no image")
        pointer = kernel32.GlobalLock(handle)
        try:
            return C.string_at(pointer, kernel32.GlobalSize(handle))
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _snip():
    before = user32.GetClipboardSequenceNumber()
    os.startfile("ms-screenclip:")
    deadline = time.monotonic() + BOX_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if user32.GetClipboardSequenceNumber() != before and user32.IsClipboardFormatAvailable(CF_DIB):
            return dib_to_png(_clipboard_dib())
    raise ValueError("screenshot cancelled (no selection)")


def capture(kind, name=None):
    """PNG bytes. kind: box, all, or screen (name=None: the monitor under the cursor)."""
    if kind == "box":
        return _snip()
    if kind == "all":
        x, y, w, h = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))  # virtual screen
        return _grab(x, y, w, h)
    if name is None:
        point = W.POINT()
        user32.GetCursorPos(C.byref(point))
        target = _info(user32.MonitorFromPoint(point, 2))  # MONITOR_DEFAULTTONEAREST
    else:
        target = next((m for m in monitors() if m["name"] == name), None)
        if not target:
            raise ValueError(f"no screen named {name}")
    return _grab(target["x"], target["y"], target["width"], target["height"])
