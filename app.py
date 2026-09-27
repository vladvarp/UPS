"""UPS — локальный монитор питания и ИБП.

Обычный запуск: start.bat — своё окно, без консоли.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import socket
import threading
import time
from pathlib import Path

from ups import APP_NAME, APP_TAGLINE
from ups.monitor import Monitor
from ups.server import bind, serve
from ups.screenshot import capture_main_window
from ups.tray import Tray

ICON = Path(__file__).resolve().parent / "static" / "icon.ico"


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} — {APP_TAGLINE}")
    parser.add_argument("--demo", action="store_true", help="показать пример без ИБП")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", action="store_true", help="открыть в браузере вместо своего окна")
    args = parser.parse_args()
    monitor = Monitor(demo=args.demo)
    monitor.start()
    if args.browser:
        serve(monitor, port=args.port, open_browser=True)
        return
    _run_window(monitor, args.port)


def _run_window(monitor: Monitor, port: int) -> None:
    try:
        import webview
    except ImportError:
        _alert("Не хватает библиотеки окна.\nВыполните в папке программы:\npy -3 -m pip install pywebview")
        monitor.stop()
        return
    _claim_app_id()
    try:
        httpd, url = bind(monitor, port=port)
    except SystemExit as exc:
        _alert(str(exc))
        monitor.stop()
        return
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    if not _wait_ready(port):
        httpd.shutdown()
        httpd.server_close()
        monitor.stop()
        _alert(f"Сервер {APP_NAME} не успел запуститься.")
        return
    window = webview.create_window(
        APP_NAME,
        url,
        width=1320,
        height=860,
        min_size=(960, 640),
        text_select=True,
    )
    tray = Tray(window)
    monitor.set_desktop_quit(tray.apply)
    monitor.set_app_restart(_restart_program)
    tray.set_before_quit(monitor.announce_close)
    monitor.set_screenshot(lambda: capture_main_window(window))
    window.events.closing += tray.on_closing
    window.events.minimized += tray.on_minimized
    if ICON.is_file():
        webview.start(icon=str(ICON))
    else:
        webview.start()
    tray.stop()
    httpd.shutdown()
    httpd.server_close()
    monitor.stop()


def _claim_app_id() -> None:
    # Without this Windows groups the window with pythonw and keeps the snake icon.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("local.UPS.monitor")
    except Exception:
        pass


def _wait_ready(port: int, tries: int = 40) -> bool:
    for _ in range(tries):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _restart_program() -> None:
    bat = Path(__file__).resolve().parent / "start.bat"
    os.startfile(str(bat))


def _alert(text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
    except Exception:
        print(text)


if __name__ == "__main__":
    main()
