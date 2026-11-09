"""Branding assets and native window identity, without touching live services."""
import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch

from scripts import launcher
from scripts import launcher_windows as windows

ASSETS = Path(__file__).resolve().parents[1] / "assets"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


class IconAssetTests(unittest.TestCase):
    def test_png_dimensions_and_alpha(self):
        for size in (16, 32, 48, 64, 256):
            data = (ASSETS / f"launcher-{size}.png").read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack_from(">II", data, 16), (size, size))
            self.assertEqual(data[25], 6)  # RGBA, not an opaque flattened tile.

    def test_ico_contains_all_high_dpi_sizes(self):
        data = (ASSETS / "launcher.ico").read_bytes()
        self.assertEqual(struct.unpack_from("<HHH", data), (0, 1, len(SIZES)))
        found = []
        for index in range(len(SIZES)):
            width, height, _, _, planes, bits, length, offset = struct.unpack_from(
                "<BBBBHHII", data, 6 + index * 16)
            size = width or 256
            self.assertEqual(height or 256, size)
            self.assertEqual((planes, bits), (0, 32))
            self.assertLessEqual(offset + length, len(data))
            self.assertEqual(struct.unpack_from("<I", data, offset)[0], 40)
            self.assertEqual(struct.unpack_from("<ii", data, offset + 4), (size, size * 2))
            found.append(size)
        self.assertEqual(tuple(found), SIZES)

    def test_identity_is_set_before_tk_window_creation(self):
        events = []
        with patch.object(launcher, "set_taskbar_identity", side_effect=lambda: events.append("identity")), \
                patch("tkinter.Tk", side_effect=lambda: events.append("window") or Mock()), \
                patch.object(launcher, "LauncherApp"):
            self.assertEqual(launcher.main([]), 0)
        self.assertEqual(events, ["identity", "window"])

    def test_background_supervisor_does_not_set_ui_identity(self):
        with patch.object(launcher, "set_taskbar_identity") as identity, \
                patch.object(launcher, "supervise_background", return_value=0):
            self.assertEqual(launcher.main(["--background"]), 0)
        identity.assert_not_called()

    def test_unavailable_shell_identity_is_nonfatal(self):
        if os.name != "nt":
            self.assertFalse(windows.set_taskbar_identity())
            return
        with patch.object(ctypes, "WinDLL", side_effect=OSError):
            self.assertFalse(windows.set_taskbar_identity())

    def test_iconphoto_retains_all_resolution_images(self):
        import tkinter as tk
        root = Mock()
        images = [Mock() for _ in range(5)]
        with patch.object(tk, "PhotoImage", side_effect=images):
            self.assertIs(launcher.apply_launcher_icon(root), images[1])
        root.iconphoto.assert_called_once_with(False, *images)
        self.assertEqual(root._launcher_icons, tuple(images))

    def test_png_failure_falls_back_to_windows_ico(self):
        import tkinter as tk
        root = Mock()
        with patch.object(tk, "PhotoImage", side_effect=tk.TclError("synthetic PNG unavailable")):
            self.assertIsNone(launcher.apply_launcher_icon(root))
        root.iconbitmap.assert_called_once_with(str(ASSETS / "launcher.ico"))

    def test_missing_assets_do_not_disable_launcher(self):
        import tkinter as tk
        root = Mock()
        root.iconbitmap.side_effect = tk.TclError("synthetic missing icon")
        with patch.object(tk, "PhotoImage", side_effect=tk.TclError("synthetic missing PNG")):
            self.assertIsNone(launcher.apply_launcher_icon(root))


@unittest.skipUnless(os.name == "nt", "Windows window icons")
class NativeIconTests(unittest.TestCase):
    def test_shell_reads_back_launcher_identity(self):
        self.assertTrue(windows.set_taskbar_identity())
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.GetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.POINTER(W.LPWSTR)]
        shell.GetCurrentProcessExplicitAppUserModelID.restype = W.LONG
        value = W.LPWSTR()
        self.assertEqual(shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(value)), 0)
        try:
            self.assertEqual(value.value, windows.APP_USER_MODEL_ID)
        finally:
            ole = ctypes.WinDLL("ole32")
            ole.CoTaskMemFree.argtypes, ole.CoTaskMemFree.restype = [ctypes.c_void_p], None
            ole.CoTaskMemFree(value)

    def test_tk_loads_custom_small_and_large_icons(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            heading = launcher.apply_launcher_icon(root)
            root.update_idletasks()
            self.assertEqual((heading.width(), heading.height()), (32, 32))
            self.assertTrue(heading.transparency_get(0, 0))
            self.assertTrue(all(channel >= 245 for channel in heading.get(8, 12)))
            root.attributes("-alpha", 0)
            root.geometry("200x100+20000+20000")
            root.deiconify()
            root.update()
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.GetParent.argtypes, user.GetParent.restype = [W.HWND], W.HWND
            user.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
            user.SendMessageW.restype = ctypes.c_ssize_t
            user.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT]
            user.LoadImageW.restype = W.HANDLE
            user.DestroyIcon.argtypes, user.DestroyIcon.restype = [W.HANDLE], W.BOOL
            window = user.GetParent(root.winfo_id())
            self.assertTrue(window)
            for size in (0, 1):
                handle = user.SendMessageW(window, 0x007F, size, 0)  # WM_GETICON
                self.assertTrue(handle)
                width, height, actual = self.icon_pixels(handle)
                expected = user.LoadImageW(None, str(ASSETS / "launcher.ico"), 1, width, height, 0x10)
                self.assertTrue(expected)
                try:
                    expected_width, expected_height, target = self.icon_pixels(expected)
                    self.assertEqual((width, height), (expected_width, expected_height))
                    with self.subTest(size=size):
                        self.assertTrue(actual == target, "native-icon-pixels-mismatch")
                finally:
                    user.DestroyIcon(expected)
        finally:
            root.destroy()

    @staticmethod
    def icon_pixels(handle):
        class IconInfo(ctypes.Structure):
            _fields_ = [("icon", W.BOOL), ("x", W.DWORD), ("y", W.DWORD),
                        ("mask", W.HBITMAP), ("color", W.HBITMAP)]

        class Bitmap(ctypes.Structure):
            _fields_ = [("kind", W.LONG), ("width", W.LONG), ("height", W.LONG),
                        ("stride", W.LONG), ("planes", W.WORD), ("bits", W.WORD), ("data", ctypes.c_void_p)]

        class BitmapHeader(ctypes.Structure):
            _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                        ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD),
                        ("image_size", W.DWORD), ("x", W.LONG), ("y", W.LONG),
                        ("colors", W.DWORD), ("important", W.DWORD)]

        user, gdi = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
        user.GetIconInfo.argtypes, user.GetIconInfo.restype = [W.HANDLE, ctypes.POINTER(IconInfo)], W.BOOL
        gdi.GetObjectW.argtypes, gdi.GetObjectW.restype = [W.HANDLE, ctypes.c_int, ctypes.c_void_p], ctypes.c_int
        gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [W.HDC], W.HDC
        gdi.DeleteDC.argtypes, gdi.DeleteDC.restype = [W.HDC], W.BOOL
        gdi.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT]
        gdi.GetDIBits.restype = ctypes.c_int
        gdi.DeleteObject.argtypes, gdi.DeleteObject.restype = [W.HANDLE], W.BOOL
        info, bitmap = IconInfo(), Bitmap()
        if not user.GetIconInfo(handle, ctypes.byref(info)):
            raise AssertionError("Native icon readback failed")
        try:
            if not gdi.GetObjectW(info.color, ctypes.sizeof(bitmap), ctypes.byref(bitmap)):
                raise AssertionError("Native icon bitmap missing")
            length = bitmap.width * 4 * bitmap.height
            pixels = ctypes.create_string_buffer(length)
            header = BitmapHeader(size=ctypes.sizeof(BitmapHeader), width=bitmap.width,
                                  height=-bitmap.height, planes=1, bits=32)
            dc = gdi.CreateCompatibleDC(None)
            try:
                if gdi.GetDIBits(dc, info.color, 0, bitmap.height, pixels, ctypes.byref(header), 0) != bitmap.height:
                    raise AssertionError("Native icon pixels unavailable")
            finally:
                gdi.DeleteDC(dc)
            return bitmap.width, bitmap.height, pixels.raw
        finally:
            gdi.DeleteObject(info.mask)
            gdi.DeleteObject(info.color)


if __name__ == "__main__":
    unittest.main()
