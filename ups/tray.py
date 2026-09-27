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


class Tray:
    def __init__(self, window) -> None:
        self.window = window
        self.force_quit = False
        self._icon = None
        self._before_quit = None

    def set_before_quit(self, handler) -> None:
        self._before_quit = handler

    def on_closing(self) -> bool | None:
        if self.force_quit:
            self.stop()
            return None
        threading.Thread(target=self._open_dialog, daemon=True).start()
        return False

    def on_minimized(self) -> None:
        threading.Thread(target=self._to_tray, daemon=True).start()

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
        menu = pystray.Menu(
            pystray.MenuItem("Открыть", self.restore, default=True),
            pystray.MenuItem("Закрыть", self.quit),
        )
        self._icon = pystray.Icon("UPS", image, APP_NAME, menu)
        self._icon.run_detached()

    def restore(self, icon=None, item=None) -> None:
        try:
            self.window.show()
            self.window.restore()
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
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
