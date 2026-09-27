"""Capture the UPS overview as a borderless fullscreen shot."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from io import BytesIO

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0
SW_HIDE = 0
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_SHOWNOACTIVATE = 4
SW_SHOWMINNOACTIVE = 7
SW_RESTORE = 9
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
HWND_TOP = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
RDW_INVALIDATE = 0x0001
RDW_ERASE = 0x0004
RDW_ALLCHILDREN = 0x0080
RDW_UPDATENOW = 0x0100
RDW_FRAME = 0x0400
GWL_STYLE = -16
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
MONITOR_DEFAULTTONEAREST = 2
SM_CXSCREEN = 0
SM_CYSCREEN = 1
DEFAULT_W = 1320
DEFAULT_H = 860
_PTR = ctypes.c_ssize_t

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype = wintypes.BOOL


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", ctypes.c_uint32),
    ]


def saved_window_geom(settings: dict) -> dict:
    width = _int_or(settings.get("window_width"), DEFAULT_W)
    height = _int_or(settings.get("window_height"), DEFAULT_H)
    width = max(960, min(width, 8000))
    height = max(640, min(height, 5000))
    x = settings.get("window_x")
    y = settings.get("window_y")
    has_pos = x is not None and y is not None
    if has_pos:
        x, y = _clamp_geom(_int_or(x, 0), _int_or(y, 0), width, height)
    return {
        "width": width,
        "height": height,
        "x": x if has_pos else None,
        "y": y if has_pos else None,
        "maximized": bool(settings.get("window_maximized")),
    }


def read_window_geom(window) -> dict | None:
    hwnd = hwnd_from_webview(window)
    if not hwnd:
        return None
    place = WINDOWPLACEMENT()
    place.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(place)):
        return None
    rect = place.rcNormalPosition
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width < 200 or height < 160:
        return None
    x, y = _clamp_geom(int(rect.left), int(rect.top), width, height)
    return {
        "window_x": x,
        "window_y": y,
        "window_width": max(960, min(width, 8000)),
        "window_height": max(640, min(height, 5000)),
        "window_maximized": int(place.showCmd) == SW_SHOWMAXIMIZED,
    }


def _int_or(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _clamp_geom(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw <= 0 or vh <= 0:
        return x, y
    x = min(max(x, vx - width + 80), vx + vw - 80)
    y = min(max(y, vy), vy + vh - 80)
    return x, y


def hwnd_from_webview(window) -> int | None:
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None) if native is not None else None
    if handle is None:
        return None
    try:
        if hasattr(handle, "ToInt64"):
            return int(handle.ToInt64())
        return int(handle)
    except (TypeError, ValueError):
        return None


def capture_main_window(window) -> bytes | None:
    hwnd = hwnd_from_webview(window) or _find_ups_window()
    if not hwnd:
        return None
    snapshot = _snapshot_window(hwnd)
    _enter_fullscreen(hwnd)
    previous_tab = _prepare_overview(window)
    time.sleep(0.45)
    png = _capture_content(hwnd)
    if _image_is_blank(png):
        time.sleep(0.4)
        previous_tab = previous_tab or _prepare_overview(window)
        png = _capture_content(hwnd)
    _restore_tab(window, previous_tab)
    _restore_window(window, hwnd, snapshot)
    if _image_is_blank(png):
        return None
    return _trim_letterbox(png) or png


def capture_hwnd(hwnd: int | None) -> bytes | None:
    if not hwnd:
        hwnd = _find_ups_window()
    if not hwnd:
        return None
    return _capture_content(hwnd)


def _capture_content(hwnd: int) -> bytes | None:
    target = _largest_child(hwnd) or hwnd
    width, height = _pixel_size(target)
    if width < 40 or height < 40:
        width, height = _pixel_size(hwnd)
    if width < 40 or height < 40:
        width, height = DEFAULT_W, DEFAULT_H
    png = _png_from_raw(_print_window(target, width, height), width, height)
    if not _image_is_blank(png):
        return png
    if target != hwnd:
        width, height = _pixel_size(hwnd)
        if width >= 40 and height >= 40:
            return _png_from_raw(_print_window(hwnd, width, height), width, height)
    return png


def _png_from_raw(raw: bytes | None, width: int, height: int) -> bytes | None:
    if raw is None:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    image = Image.frombuffer("RGB", (width, height), raw, "raw", "BGRX", 0, 1)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _trim_letterbox(png: bytes | None) -> bytes | None:
    if not png:
        return None
    try:
        from PIL import Image
    except ImportError:
        return png
    image = Image.open(BytesIO(png)).convert("RGB")
    mask = image.convert("L").point(lambda pixel: 255 if pixel > 14 else 0)
    box = mask.getbbox()
    if not box:
        return png
    left, top, right, bottom = box
    pad = 8
    crop = (
        max(0, left - pad),
        max(0, top - pad),
        min(image.width, right + pad),
        min(image.height, bottom + pad),
    )
    if crop[2] - crop[0] < 240 or crop[3] - crop[1] < 160:
        return png
    trimmed = image.crop(crop)
    buffer = BytesIO()
    trimmed.save(buffer, format="PNG")
    return buffer.getvalue()


def _image_is_blank(png: bytes | None) -> bool:
    if not png:
        return True
    try:
        from PIL import Image
    except ImportError:
        return False
    image = Image.open(BytesIO(png)).convert("L").resize((48, 48))
    darkest, lightest = image.getextrema()
    return lightest - darkest < 8 and lightest < 18


def _prepare_overview(window) -> str:
    if window is None:
        return ""
    try:
        result = window.evaluate_js("window.prepareForScreenshot && window.prepareForScreenshot()")
        return str(result or "").strip().strip("'\"")
    except Exception:
        return ""


def _restore_tab(window, tab: str) -> None:
    if window is None:
        return
    name = tab or "overview"
    try:
        window.evaluate_js(f"window.restoreAfterScreenshot && window.restoreAfterScreenshot({name!r})")
    except Exception:
        pass


def _snapshot_window(hwnd: int) -> dict:
    place = WINDOWPLACEMENT()
    place.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(hwnd, ctypes.byref(place))
    return {
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "iconic": bool(user32.IsIconic(hwnd)),
        "style": _get_style(hwnd),
        "placement": place,
    }


def _enter_fullscreen(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    _set_style(hwnd, _borderless_style(_get_style(hwnd)))
    left, top, width, height = _screen_rect(hwnd)
    user32.SetWindowPos(
        hwnd,
        HWND_TOP,
        left,
        top,
        width,
        height,
        SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOACTIVATE,
    )
    user32.RedrawWindow(
        hwnd,
        None,
        None,
        RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW | RDW_FRAME,
    )


def _restore_window(window, hwnd: int, snapshot: dict) -> None:
    _set_style(hwnd, int(snapshot.get("style") or 0))
    place = snapshot.get("placement")
    if isinstance(place, WINDOWPLACEMENT):
        user32.SetWindowPlacement(hwnd, ctypes.byref(place))
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED | SWP_NOACTIVATE)
    if snapshot.get("iconic"):
        user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        return
    if not snapshot.get("visible"):
        if window is not None:
            try:
                window.hide()
                return
            except Exception:
                pass
        user32.ShowWindow(hwnd, SW_HIDE)


def _borderless_style(style: int) -> int:
    drop = (
        WS_CAPTION
        | WS_THICKFRAME
        | WS_SYSMENU
        | WS_MINIMIZEBOX
        | WS_MAXIMIZEBOX
        | WS_BORDER
        | WS_DLGFRAME
    )
    return int(style) & ~drop


def _get_style(hwnd: int) -> int:
    getter = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    getter.restype = _PTR
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    return int(getter(hwnd, GWL_STYLE))


def _set_style(hwnd: int, style: int) -> None:
    setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    setter.restype = _PTR
    setter.argtypes = [wintypes.HWND, ctypes.c_int, _PTR]
    setter(hwnd, GWL_STYLE, style)


def _screen_rect(hwnd: int) -> tuple[int, int, int, int]:
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        area = info.rcMonitor
        return area.left, area.top, area.right - area.left, area.bottom - area.top
    width = user32.GetSystemMetrics(SM_CXSCREEN) or DEFAULT_W
    height = user32.GetSystemMetrics(SM_CYSCREEN) or DEFAULT_H
    return 0, 0, int(width), int(height)


def _pixel_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    if user32.GetClientRect(hwnd, ctypes.byref(rect)):
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width >= 40 and height >= 40:
            return width, height
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width >= 40 and height >= 40:
            return width, height
    return 0, 0


def _largest_child(hwnd: int) -> int | None:
    found: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(child, _lparam):
        rect = wintypes.RECT()
        if user32.GetClientRect(child, ctypes.byref(rect)):
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            if area > 0:
                found.append((area, int(child)))
        return True

    user32.EnumChildWindows(hwnd, visit, 0)
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][1]


def _print_window(hwnd: int, width: int, height: int) -> bytes | None:
    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    painted = user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
    if not painted:
        gdi32.BitBlt(memory_dc, 0, 0, width, height, window_dc, 0, 0, SRCCOPY)
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = BI_RGB
    raw = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, raw, ctypes.byref(info), DIB_RGB_COLORS)
    gdi32.SelectObject(memory_dc, previous)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)
    return raw.raw


def _find_ups_window() -> int | None:
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title == "UPS" or title.endswith(" · UPS") or title.endswith("UPS"):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None
