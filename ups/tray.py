"""In-app close dialog and the Windows tray icon."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from ups import APP_NAME

CLOSE = "close"
TRAY = "tray"
STAY = "stay"
_ICON = Path(__file__).resolve().parent.parent / "static" / "icon.png"
_WM_LBUTTONUP = 0x0202
_WM_LBUTTONDBLCLK = 0x0203
_WM_NOTIFY = 0x040B


class Tray:
    def __init__(self, window) -> None:
        self.window = window
        self.force_quit = False
        self.hidden = False
        self.unread = 0
        self._icon = None
        self._before_quit = None
        self._base_image = None
        self._click_at = 0.0
        self._click_timer: threading.Timer | None = None
        self._ignore_up_until = 0.0
        self._notices: list[dict] = []
        self._events_source = None
        self._inbox_cleared = False

    def set_before_quit(self, handler) -> None:
        self._before_quit = handler

    def set_events_source(self, handler) -> None:
        self._events_source = handler

    def on_closing(self) -> bool | None:
        if self.force_quit:
            self.stop()
            return None
        threading.Thread(target=self._open_dialog, daemon=True).start()
        return False

    def on_minimized(self) -> None:
        self.hidden = True
        threading.Thread(target=self._to_tray, daemon=True).start()

    def on_restored(self) -> None:
        self.hidden = False
        self._ask_reload_history()

    def _open_dialog(self) -> None:
        time.sleep(0.05)
        try:
            self.window.evaluate_js("window.showQuitDialog && window.showQuitDialog()")
        except Exception:
            pass

    def apply(self, choice: str) -> None:
        # Must not touch the window on the HTTP thread: hide()/destroy()
        # Invoke the GUI and wait, while the page is still awaiting this request.
        threading.Thread(target=self._apply_later, args=(str(choice),), daemon=True).start()

    def _apply_later(self, choice: str) -> None:
        time.sleep(0.2)
        if choice == CLOSE:
            self._finish_close()
            return
        if choice == TRAY:
            self._to_tray()

    def _to_tray(self) -> None:
        time.sleep(0.05)
        try:
            self.window.hide()
        except Exception:
            pass
        self.hidden = True
        self.start()

    def start(self) -> None:
        if self._icon is not None:
            return
        try:
            import pystray
            from PIL import Image
        except ImportError:
            return
        image = Image.open(_ICON) if _ICON.is_file() else Image.new("RGBA", (32, 32), (201, 164, 106, 255))
        self._base_image = image.convert("RGBA")
        menu = pystray.Menu(
            pystray.MenuItem("Уведомления", self.open_notices),
            pystray.MenuItem("Открыть", self.restore),
            pystray.MenuItem("Закрыть", self.quit),
        )
        self._icon = pystray.Icon("UPS", self._icon_image(), self._icon_title(), menu)
        self._bind_icon_clicks()
        self._icon.run_detached()

    def _bind_icon_clicks(self) -> None:
        icon = self._icon
        if icon is None:
            return
        original = icon._on_notify

        def hooked(wparam, lparam):
            if lparam == _WM_LBUTTONDBLCLK:
                self._on_icon_dblclick()
                return 0
            if lparam == _WM_LBUTTONUP:
                self._on_icon_click()
                return 0
            return original(wparam, lparam)

        icon._on_notify = hooked
        handlers = getattr(icon, "_message_handlers", None)
        if isinstance(handlers, dict):
            handlers[_WM_NOTIFY] = hooked

    def _on_icon_click(self, icon=None, item=None) -> None:
        if time.monotonic() < self._ignore_up_until:
            return
        if self._click_timer is not None:
            self._click_timer.cancel()
            self._click_timer = None
            self.restore()
            return
        self._click_at = time.monotonic()
        self._click_timer = threading.Timer(double_click_wait(), self.open_notices)
        self._click_timer.daemon = True
        self._click_timer.start()

    def _on_icon_dblclick(self, icon=None, item=None) -> None:
        self._ignore_up_until = time.monotonic() + 0.45
        if self._click_timer:
            self._click_timer.cancel()
            self._click_timer = None
        self.restore()

    def restore(self, icon=None, item=None) -> None:
        if self._click_timer:
            self._click_timer.cancel()
            self._click_timer = None
        self.hidden = False
        try:
            from ups.win_toast import close_inbox
            close_inbox()
        except Exception:
            pass
        show_main_window(self.window)
        try:
            self.window.evaluate_js("window.closeNoticeInbox && window.closeNoticeInbox()")
        except Exception:
            pass
        self._ask_reload_history()

    def open_notices(self, icon=None, item=None) -> None:
        if self._click_timer:
            self._click_timer.cancel()
            self._click_timer = None
        self.unread = 0
        self._refresh_icon()
        try:
            from ups.win_toast import toggle_inbox
            toggle_inbox(self.inbox_items(), on_open=self.restore, on_clear=self.clear_notices)
        except Exception:
            pass

    def inbox_items(self) -> list[dict]:
        if self._inbox_cleared:
            return list(self._notices)
        if self._events_source:
            try:
                items = [as_notice(item) for item in (self._events_source() or [])[:40]]
                if items:
                    return items
            except Exception:
                pass
        return list(self._notices)

    def clear_notices(self) -> None:
        self._notices = []
        self._inbox_cleared = True
        self.unread = 0
        self._refresh_icon()

    def remember_notice(self, title: str, message: str, code: str = "", level: str = "", t=None) -> None:
        self._inbox_cleared = False
        self._notices.insert(0, as_notice({
            "title": title,
            "message": message,
            "code": code,
            "level": level,
            "t": t or time.time(),
        }))
        self._notices = self._notices[:40]

    def _ask_reload_history(self) -> None:
        try:
            self.window.evaluate_js("window.reloadLiveHistory && window.reloadLiveHistory()")
        except Exception:
            pass

    def notify(self, title: str, message: str, image: bytes | None = None, code: str = "", level: str = "") -> None:
        if not self.hidden:
            return
        self.remember_notice(title, message, code, level)
        self.unread += 1
        self._refresh_icon()
        if image:
            self._notify_card(title, message, image)
            return
        self._balloon(title, message)

    def _icon_title(self) -> str:
        if self.unread < 1:
            return APP_NAME
        extra = "9+" if self.unread > 9 else str(self.unread)
        return f"{APP_NAME} · {extra} новых"

    def _icon_image(self):
        from PIL import Image, ImageDraw
        base = self._base_image
        icon = base.copy() if base is not None else Image.new("RGBA", (32, 32), (201, 164, 106, 255))
        if self.unread < 1:
            return icon
        width, height = icon.size
        if width < 32 or height < 32:
            icon = icon.resize((32, 32), Image.Resampling.LANCZOS)
            width, height = 32, 32
        draw = ImageDraw.Draw(icon)
        radius = max(5, height // 5)
        cx = width - radius - 1
        cy = radius + 1
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=(225, 93, 76, 255),
            outline=(255, 177, 168, 255),
        )
        return icon

    def _refresh_icon(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.icon = self._icon_image()
            icon.title = self._icon_title()
        except Exception:
            pass

    def _notify_card(self, title: str, message: str, image: bytes) -> None:
        try:
            from ups.win_toast import show_card
            if show_card(image, title, message, on_click=self.restore):
                return
        except Exception:
            pass
        self._balloon(title, message)

    def _balloon(self, title: str, message: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(str(message or title), str(title or APP_NAME))
        except Exception:
            pass

    def quit(self, icon=None, item=None) -> None:
        self._finish_close()

    def _finish_close(self) -> None:
        if self._before_quit:
            try:
                self._before_quit()
            except Exception:
                pass
        self.force_quit = True
        self.stop()
        try:
            self.window.destroy()
        except Exception:
            pass

    def stop(self) -> None:
        try:
            from ups.win_toast import close_inbox
            close_inbox()
        except Exception:
            pass
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass


def double_click_wait() -> float:
    try:
        import ctypes
        ms = int(ctypes.windll.user32.GetDoubleClickTime())
    except Exception:
        ms = 500
    return max(0.45, min(1.0, ((ms or 500) / 1000.0) + 0.08))


def show_main_window(window) -> None:
    try:
        window.show()
    except Exception:
        pass
    try:
        window.restore()
    except Exception:
        pass
    hwnd = _window_hwnd(window)
    if not hwnd:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)
        user32.ShowWindow(hwnd, 5)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _window_hwnd(window) -> int | None:
    try:
        from ups.screenshot import hwnd_from_webview
        hwnd = hwnd_from_webview(window)
        if hwnd:
            return int(hwnd)
    except Exception:
        pass
    try:
        from ups.screenshot import _find_ups_window
        hwnd = _find_ups_window()
        if hwnd:
            return int(hwnd)
    except Exception:
        pass
    return None


def as_notice(item: dict | None = None) -> dict:
    from ups.notify_art import card_title

    item = item or {}
    code = str(item.get("code") or "")
    message = str(item.get("message") or "")
    return {
        "code": code,
        "level": str(item.get("level") or ""),
        "title": str(item.get("title") or card_title(code, message) or "Событие UPS"),
        "message": message,
        "t": float(item.get("t") or 0),
    }
