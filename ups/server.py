"""Local web server. It listens on this computer only."""

from __future__ import annotations

import json
import mimetypes
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ups.monitor import Monitor

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exception(), _DISCONNECTS):
            return
        super().handle_error(request, client_address)


def bind(monitor: Monitor, host: str = "127.0.0.1", port: int = 8765) -> tuple[QuietServer, str]:
    handler = _handler(monitor)
    # On Windows SO_REUSEADDR lets a second copy bind the same port and
    # steal half the browser requests. Refuse the second copy instead.
    QuietServer.allow_reuse_address = False
    try:
        httpd = QuietServer((host, port), handler)
    except OSError as exc:
        raise SystemExit(f"Порт {port} занят ({exc}). Закройте другую копию UPS.") from exc
    return httpd, f"http://{host}:{port}"


def serve(monitor: Monitor, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    httpd, url = bind(monitor, host, port)
    print(f"UPS запущен: {url}", flush=True)
    print("Окно можно не закрывать. Остановить — Ctrl+C.", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка.")
    finally:
        httpd.server_close()
        monitor.stop()


def _handler(monitor: Monitor):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle(self) -> None:
            try:
                super().handle()
            except _DISCONNECTS:
                return

        def log_message(self, fmt: str, *args) -> None:
            if args and str(args[1]).startswith("200"):
                return
            print(f"{self.address_string()} {fmt % args}")

        def do_GET(self) -> None:
            if not _local(self.client_address[0]):
                self._json(403, {"ok": False, "message": "Только с этого компьютера"})
                return
            path = urlparse(self.path)
            route = path.path
            if route in {"/", "/index.html"}:
                self._file(STATIC / "index.html")
                return
            if route.startswith("/api/"):
                self._api_get(route, parse_qs(path.query))
                return
            rel = route.lstrip("/")
            target = (STATIC / rel).resolve()
            if STATIC.resolve() not in target.parents and target != STATIC.resolve():
                self._json(404, {"ok": False, "message": "Нет такой страницы"})
                return
            self._file(target)

        def do_POST(self) -> None:
            if not _local(self.client_address[0]):
                self._json(403, {"ok": False, "message": "Только с этого компьютера"})
                return
            route = urlparse(self.path).path
            payload = self._body()
            if route == "/api/command":
                action = str(payload.get("action", ""))
                if action == "simulate_event":
                    self._json(200, monitor.simulate_event(str(payload.get("code", ""))))
                    return
                self._json(200, monitor.command(action))
                return
            if route == "/api/settings":
                result = monitor.update_settings(payload)
                self._json(200 if result.get("ok") else 400, result)
                return
            if route == "/api/telegram/chat":
                result = monitor.update_telegram_chat(payload)
                self._json(200 if result.get("ok") else 400, result)
                return
            if route == "/api/quit":
                action = str(payload.get("action") or "close")
                self._json(200, monitor.desktop_quit(action))
                return
            if route == "/api/reports/open-folder":
                self._json(200, monitor.open_journal_folder())
                return
            self._json(404, {"ok": False, "message": "Нет такого запроса"})

        def _api_get(self, route: str, query: dict) -> None:
            if route == "/api/state":
                self._json(200, monitor.state())
                return
            if route == "/api/stream":
                self._stream()
                return
            if route == "/api/history":
                minutes = int((query.get("minutes") or ["60"])[0] or 60)
                self._json(200, monitor.history(minutes))
                return
            if route == "/api/events":
                self._json(200, monitor.events())
                return
            if route == "/api/simulations":
                self._json(200, monitor.simulations())
                return
            if route == "/api/ports":
                self._json(200, monitor.scan_ports())
                return
            if route == "/api/settings":
                self._json(200, monitor.get_settings())
                return
            if route == "/api/export.csv":
                body = monitor.export_csv().encode("utf-8-sig")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename=ups.csv")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if route == "/api/reports/meta":
                self._json(200, monitor.report_meta())
                return
            if route == "/api/reports/html":
                kind = str((query.get("kind") or ["summary"])[0] or "summary")
                date = str((query.get("date") or [""])[0] or "")
                body = monitor.report_html(kind, date).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if route == "/api/energy":
                start = (query.get("from") or ["0"])[0] or "0"
                end = (query.get("to") or ["0"])[0] or "0"
                try:
                    start_ts = float(start)
                    end_ts = float(end)
                except (TypeError, ValueError):
                    start_ts = 0.0
                    end_ts = 0.0
                self._json(200, monitor.energy(start_ts, end_ts))
                return
            self._json(404, {"ok": False, "message": "Нет такого запроса"})

        def _stream(self) -> None:
            # HTTP/1.1 without a length makes the browser buffer the whole
            # stream, so the page painted once and then never moved.
            self.protocol_version = "HTTP/1.0"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b":" + b" " * 2048 + b"\n\n")
            self.wfile.flush()
            last = ""
            try:
                while True:
                    payload = json.dumps(monitor.state(), ensure_ascii=False)
                    if payload != last:
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        last = payload
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time_sleep(0.5)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                return

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > 65536:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path) -> None:
            if not path.is_file():
                self._json(404, {"ok": False, "message": "Файл не найден"})
                return
            body = path.read_bytes()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix in {".js", ".css", ".html"}:
                mime += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


_DISCONNECTS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, TimeoutError)


def _local(host: str) -> bool:
    return host in {"127.0.0.1", "::1"}


def time_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
