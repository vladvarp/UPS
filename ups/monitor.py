"""Polling loop, history and the snapshot the page renders."""

from __future__ import annotations

import csv
import io
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from ups import APP_NAME, __version__
from ups import autostart, power
from ups.protocol import (
    ProtocolError,
    battery_percent,
    battery_voltage_chaotic,
    battery_voltage_stepped,
    load_watts,
    mains_present,
    mains_voltage_active,
    mode_label,
    parse_info,
    parse_q1,
    parse_ratings,
    plausible,
    runtime_minutes,
    ups_kind,
    voltage_state,
)
from ups.transport import (
    DemoSource,
    DeviceBusy,
    TransportError,
    list_serial_ports,
    open_mega,
    probe_serial,
    scan_hid,
    upsmart_running,
)
from ups import reports
from ups.telegram import TOKEN_RE, TelegramBridge, format_events, format_report, parse_clock, status_lines

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DEFAULTS = {
    "poll_seconds": 2,
    "battery_count": 2,
    "battery_groups": 1,
    "battery_ah": 12,
    "ups_kva": 0.7,
    "full_voltage": 13.5,
    "eod_voltage": 10.5,
    "battery_health": 100,
    "sound": True,
    "pc_shutdown": False,
    "shutdown_delay": 180,
    "serial_port": "",
    "telegram_token": "",
    "telegram_pair": "",
    "telegram_chats": [],
    "telegram_notify_power": True,
    "telegram_notify_alerts": True,
    "telegram_digest": False,
    "telegram_digest_time": "21:00",
    "journal_enabled": True,
    "journal_dir": "",
}

_LIMITS = {
    "poll_seconds": (1, 30),
    "battery_count": (1, 8),
    "battery_groups": (1, 6),
    "battery_ah": (1, 300),
    "ups_kva": (0.2, 20),
    "full_voltage": (12, 15),
    "eod_voltage": (9, 13),
    "battery_health": (10, 100),
    "shutdown_delay": (30, 3600),
}


class Monitor:
    def __init__(self, demo: bool = False) -> None:
        DATA.mkdir(exist_ok=True)
        self.settings = _load_settings()
        self._demo_forced = demo
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._commands: list[str] = []
        self._thread: threading.Thread | None = None
        self._transport = None
        self._demo = DemoSource()
        self._mode = "searching"
        self._message = "Ищу бесперебойник…"
        self._reading = None
        self._info = None
        self._ratings = None
        self._info_at = 0.0
        self._trace: list[dict] = []
        self._events: list[dict] = []
        self._edges: dict[str, bool] = {}
        self._outage_started: float | None = None
        self._shutdown_at: float | None = None
        self._shutdown_sent = False
        self._shutdown_held = False
        self._session_started = time.time()
        self._last_ok = 0.0
        self._live_since = 0.0
        self._read_fails = 0
        self._last_raw = ""
        self._same_frames = 0
        self._frozen = False
        self._frozen_since = 0.0
        self._recover_stage = 0
        self._battery_level: int | None = None
        self._battery_level_at = 0.0
        self._demo_points: list[dict] = []
        self._blocker = False
        self._blocker_checked = 0.0
        self._serial_checked = 0.0
        self._ports: list[dict] = []
        self._ports_at = 0.0
        self._db = sqlite3.connect(DATA / "history.sqlite", check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._init_db()
        self._load_events()
        if not self.settings.get("telegram_pair"):
            self.settings["telegram_pair"] = f"{secrets.randbelow(1_000_000):06d}"
            _save_settings(self.settings)
        self._telegram = TelegramBridge(self)
        self._desktop_quit = None
        self._app_restart = None
        self._close_announced = False
        self._restarting = False
        self._screenshot = None
        self._batt_samples: list[float] = []
        self._pack_swap: dict | None = None
        self._pack_swap_to: float | None = None
        self._ignore_pack_until = 0.0
        self._journal_at = 0.0
        self._journal_day = ""
        if not self.settings.get("journal_dir"):
            self.settings["journal_dir"] = str(reports.default_journal_dir())
            _save_settings(self.settings)
        if demo:
            self._enter_demo()

    def set_desktop_quit(self, handler) -> None:
        self._desktop_quit = handler

    def set_screenshot(self, handler) -> None:
        self._screenshot = handler

    def set_app_restart(self, handler) -> None:
        self._app_restart = handler

    def telegram_screenshot(self) -> bytes | None:
        if self._screenshot is None:
            return None
        return self._screenshot()

    def desktop_quit(self, action: str) -> dict:
        if self._desktop_quit is None:
            return {"ok": False, "message": "Окно недоступно"}
        self._desktop_quit(str(action or ""))
        return {"ok": True}

    def _blocker_cached(self) -> bool:
        if time.time() - self._blocker_checked > 5:
            self._blocker = upsmart_running()
            self._blocker_checked = time.time()
        return self._blocker

    def start(self) -> None:
        self._telegram.start()
        self._thread = threading.Thread(target=self._loop, name="ups-monitor", daemon=True)
        self._thread.start()

    def announce_close(self) -> None:
        if self._close_announced:
            return
        self._close_announced = True
        try:
            self._telegram.announce_stop(5)
        except Exception:
            pass

    def stop(self) -> None:
        self.announce_close()
        self._stop.set()
        self._wake.set()
        self._telegram.stop()
        if self._thread:
            self._thread.join(timeout=3)
        try:
            self._flush_journal(force=True)
        except Exception:
            pass
        self._close_transport()
        self._db.close()

    def command(self, action: str) -> dict:
        known = {
            "rescan", "test", "cancel_test", "beeper", "demo", "live", "simulate",
            "cancel_shutdown", "pc_off", "pc_reboot", "pc_sleep", "pc_hibernate",
            "app_restart", "telegram_unlink", "telegram_new_pair", "telegram_clear",
        }
        if action not in known:
            return {"ok": False, "message": "Неизвестная команда"}
        if action == "app_restart":
            if self._app_restart is None:
                return {"ok": False, "message": "Перезапуск недоступен"}
            if self._restarting:
                return {"ok": True, "message": "Уже перезапускаюсь"}
            self._restarting = True
            self._event("warn", "app_restart", "Программа UPS перезапускается")
            threading.Thread(target=self._app_restart, daemon=True).start()
            return {"ok": True, "message": "Программа перезапускается"}
        slot: dict = {}
        done = threading.Event()
        with self._lock:
            self._commands.append((action, slot, done))
        self._wake.set()
        # Test / beeper must not freeze the page: the poll thread sends them
        # and the next Q1 lights the lamps. Waiting here stalled the UI.
        if action in {"test", "cancel_test", "beeper"}:
            return {
                "ok": True,
                "message": {
                    "test": "Команда теста ушла. Если мост живой, лампа «Тест» загорится через 1–2 с.",
                    "cancel_test": "Отмена теста отправлена.",
                    "beeper": "Команда зуммера ушла.",
                }[action],
            }
        if not done.wait(8):
            return {"ok": False, "message": "ИБП слишком долго не отвечал"}
        return slot.get("result") or {"ok": False, "message": "Нет ответа"}

    def update_settings(self, payload: dict) -> dict:
        clean, error = _validate(payload)
        if error:
            return {"ok": False, "message": error}
        want_autostart = clean.pop("autostart", None)
        if want_autostart is not None:
            try:
                if want_autostart:
                    autostart.enable()
                else:
                    autostart.disable()
            except OSError as exc:
                return {"ok": False, "message": f"Автозагрузку не удалось сменить: {exc}"}
        with self._lock:
            if "telegram_token" in clean and not clean["telegram_token"]:
                clean.pop("telegram_token")
            self.settings.update(clean)
            if not self.settings.get("telegram_pair"):
                self.settings["telegram_pair"] = f"{secrets.randbelow(1_000_000):06d}"
            _save_settings(self.settings)
            snapshot = dict(self.settings)
        self._telegram.configure(snapshot)
        return {"ok": True, "message": "Настройки сохранены", "settings": _public_settings(snapshot)}

    def get_settings(self) -> dict:
        with self._lock:
            return _public_settings(self.settings)

    def get_settings_raw(self) -> dict:
        with self._lock:
            return dict(self.settings)

    def link_telegram_chat(self, chat: dict) -> None:
        with self._lock:
            chats = [item for item in self.settings.get("telegram_chats") or [] if isinstance(item, dict)]
            chat_id = int(chat["id"])
            if not any(int(item.get("id") or 0) == chat_id for item in chats):
                chats.append({"id": chat_id, "name": str(chat.get("name") or "")[:80]})
            self.settings["telegram_chats"] = chats
            _save_settings(self.settings)

    def telegram_status_text(self) -> str:
        with self._lock:
            reading = None if self._reading is None else dict(self._reading)
            message = self._message
        return "\n".join([f"{APP_NAME} · {message}", *status_lines(reading)])

    def telegram_report_text(self, hours: int) -> str:
        hours = max(1, min(int(hours), 24 * 14))
        since = time.time() - hours * 3600
        points = self.history(hours * 60).get("points") or []
        with self._lock:
            events = [item for item in self._events if item["t"] >= since]
            reading = None if self._reading is None else dict(self._reading)
        return format_report(hours, points, events, reading)

    def telegram_events_text(self) -> str:
        with self._lock:
            events = list(self._events)
        return format_events(events)

    def telegram_day_html(self, stamp: str | None = None) -> tuple[bytes | None, str]:
        day = self._day_report(stamp)
        if not day["points"] and not day["events"]:
            return None, "За этот день записей ещё нет."
        name = f"UPS-{day['date']}.html"
        return reports.render_html(day).encode("utf-8"), name

    def telegram_summary_html(self) -> tuple[bytes | None, str]:
        summary = self._summary_report()
        if not summary.get("days"):
            return None, "Сводка ещё пустая — журнал только начал писаться."
        return reports.render_html(summary).encode("utf-8"), "UPS-svodka.html"

    def report_html(self, kind: str, stamp: str | None = None) -> str:
        if kind == "day":
            return reports.render_html(self._day_report(stamp))
        return reports.render_html(self._summary_report())

    def report_meta(self) -> dict:
        with self._lock:
            settings = dict(self.settings)
        root = reports.resolve_journal_dir(settings.get("journal_dir"))
        days = reports.list_days(root)
        today = reports.today_stamp()
        if today not in days:
            days = sorted(set(days + [today]))
        return {
            "enabled": bool(settings.get("journal_enabled", True)),
            "dir": str(root),
            "days": days,
            "date": today,
        }

    def open_journal_folder(self) -> dict:
        with self._lock:
            root = reports.resolve_journal_dir(self.settings.get("journal_dir"))
        try:
            reports.open_folder(root)
        except OSError as exc:
            return {"ok": False, "message": f"Папку не открыть: {exc}"}
        return {"ok": True, "message": f"Открываю {root}", "dir": str(root)}

    def state(self) -> dict:
        if self._mode == "blocked":
            self._blocker_cached()
        if time.time() - self._ports_at > 30:
            self._ports = list_serial_ports()
            self._ports_at = time.time()
        with self._lock:
            reading = None if self._reading is None else dict(self._reading)
            link = None if self._transport is None else self._transport.label
            payload = {
                "version": __version__,
                "mode": self._mode,
                "link": link,
                "message": self._message,
                "blocker": "UPSmart" if self._mode == "blocked" and self._blocker else None,
                "frozen": self._frozen,
                "frozen_for": int(time.time() - self._frozen_since) if self._frozen else 0,
                "battery_level": self._battery_level,
                "reading": reading,
                "info": self._info,
                "ratings": self._ratings,
                "trace": list(self._trace[-12:]),
                "session_seconds": int(time.time() - self._session_started),
                "settings": _public_settings(self.settings),
                "ports": list(self._ports),
            }
        payload["telegram"] = self._telegram.status()
        return payload

    def history(self, minutes: int) -> dict:
        minutes = max(1, min(int(minutes), 10080))
        since = time.time() - minutes * 60
        with self._lock:
            mode = self._mode
        if mode == "demo":
            with self._lock:
                points = [point for point in self._demo_points if point["t"] >= since and _sane_point(point)]
            return {"points": points}
        rows = self._db.execute(
            """
            SELECT ts, input_v, output_v, load_pct, battery_v, battery_pct,
                   frequency, temperature
            FROM readings
            WHERE ts >= ? AND source = 'live'
            ORDER BY ts
            """,
            (since,),
        ).fetchall()
        points = [
            {
                "t": row[0],
                "in": row[1],
                "out": row[2],
                "load": row[3],
                "batt": row[4],
                "soc": row[5],
                "hz": row[6],
                "temp": row[7],
            }
            for row in rows
        ]
        return {"points": _decimate([point for point in points if _sane_point(point)], 700)}

    def events(self, limit: int = 200) -> dict:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            return {"events": list(reversed(self._events[-limit:]))}

    def export_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["time", "input_v", "output_v", "load_pct", "battery_v", "battery_pct", "hz", "temp", "on_battery"]
        )
        since = time.time() - 7 * 86400
        for row in self._db.execute(
            """
            SELECT ts, input_v, output_v, load_pct, battery_v, battery_pct,
                   frequency, temperature, on_battery
            FROM readings WHERE ts >= ? AND source = 'live' ORDER BY ts
            """,
            (since,),
        ):
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[0]))
            writer.writerow([stamp, *row[1:]])
        return buf.getvalue()

    # -- loop -----------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._drain()
            if self._demo_forced or self._mode == "demo":
                self._poll_demo()
            else:
                self._poll_live()
            with self._lock:
                wait = float(self.settings["poll_seconds"])
                if getattr(self._transport, "kind", "") == "mega-usb":
                    # The bridge chip wants the UPSmart rhythm: one read per second.
                    wait = 1.0
                if self._mode in {"blocked", "missing"}:
                    wait = 3 if self._mode == "blocked" else 5
                elif self._mode == "searching" and self._transport is None:
                    wait = 5
            self._wake.wait(wait)
            self._wake.clear()

    def _drain(self) -> None:
        with self._lock:
            pending = self._commands
            self._commands = []
            mode = self._mode
        for action, slot, done in pending:
            try:
                slot["result"] = self._run_command(action, mode)
            finally:
                done.set()

    def _run_command(self, action: str, mode: str) -> dict:
        if action == "demo":
            self._enter_demo()
            return {"ok": True, "message": _command_hint(action)}
        if action == "live":
            self._demo_forced = False
            self._close_transport()
            with self._lock:
                self._mode = "searching"
                self._message = "Ищу бесперебойник…"
            return {"ok": True, "message": _command_hint(action)}
        if action == "rescan":
            self._demo_forced = False
            self._close_transport()
            with self._lock:
                if self._mode == "demo":
                    self._reading = None
                self._mode = "searching"
                self._message = "Ищу бесперебойник…"
            return {"ok": True, "message": _command_hint(action)}
        if action == "simulate" and mode == "demo":
            self._demo.outage = not self._demo.outage
            return {"ok": True, "message": "Имитация сети переключена"}
        if action == "cancel_shutdown":
            self._shutdown_held = True
            self._abort_os_shutdown()
            self._event("info", "shutdown_cancel", "Выключение компьютера отменено")
            return {"ok": True, "message": "Выключение компьютера отменено"}
        if action == "pc_off":
            power.shutdown(20, f"{APP_NAME}: выключение с телефона")
            self._shutdown_sent = True
            self._event("bad", "pc_off", "Компьютер выключится через 20 секунд")
            return {"ok": True, "message": "Компьютер выключится через 20 секунд. Можно отменить."}
        if action == "pc_reboot":
            power.restart(20, f"{APP_NAME}: перезагрузка с телефона")
            self._shutdown_sent = True
            self._event("warn", "pc_reboot", "Компьютер перезагрузится через 20 секунд")
            return {"ok": True, "message": "Компьютер перезагрузится через 20 секунд. Можно отменить."}
        if action == "pc_sleep":
            self._event("warn", "pc_sleep", "Компьютер уходит в сон")
            threading.Thread(target=lambda: (time.sleep(1.2), power.sleep()), daemon=True).start()
            return {"ok": True, "message": "Усыпляю компьютер"}
        if action == "pc_hibernate":
            self._event("warn", "pc_hibernate", "Компьютер уходит в гибернацию")
            threading.Thread(target=lambda: (time.sleep(1.2), power.hibernate()), daemon=True).start()
            return {"ok": True, "message": "Увожу компьютер в гибернацию"}
        if action == "telegram_unlink":
            with self._lock:
                self.settings["telegram_chats"] = []
                _save_settings(self.settings)
            return {"ok": True, "message": "Чаты телеграма отвязаны"}
        if action == "telegram_new_pair":
            with self._lock:
                self.settings["telegram_pair"] = f"{secrets.randbelow(1_000_000):06d}"
                _save_settings(self.settings)
                code = self.settings["telegram_pair"]
            return {"ok": True, "message": f"Новый код: {code}", "pair_code": code}
        if action == "telegram_clear":
            with self._lock:
                self.settings["telegram_token"] = ""
                self.settings["telegram_chats"] = []
                _save_settings(self.settings)
                snapshot = dict(self.settings)
            self._telegram.configure(snapshot)
            return {"ok": True, "message": "Бот выключен"}
        if mode != "live" or self._transport is None:
            text = "Команда не отправлена: нет связи с ИБП"
            self._event("warn", "no_device", text)
            return {"ok": False, "message": text}
        protocol = {"test": "T", "cancel_test": "C", "beeper": "Q"}.get(action)
        if not protocol:
            return {"ok": False, "message": "Неизвестная команда"}
        try:
            self._note("→", protocol)
            try:
                reply = self._transport.command(protocol, retries=1)
            except TransportError as exc:
                if "не принял" not in str(exc):
                    raise
                reply = "UPS No Ack"
            self._note("←", reply or "ок")
            text = _command_result(action, reply)
            self._event("info", action, text)
            return {"ok": True, "message": text}
        except (TransportError, OSError) as exc:
            text = f"Команда не прошла: {exc}"
            self._event("warn", "command_fail", text)
            return {"ok": False, "message": text}

    def _poll_demo(self) -> None:
        raw = self._demo.query_q1()
        self._accept(raw, "demo")

    def _poll_live(self) -> None:
        if self._transport is None:
            self._connect()
            if self._transport is None:
                return
        try:
            name, reply = self._transport.poll_step()
        except (TransportError, ProtocolError, OSError) as exc:
            self._read_fails += 1
            self._note("!", str(exc))
            # A blank frame or a slow USB answer is normal. Keep the last
            # real numbers on screen and try again on the same connection.
            with self._lock:
                if self._reading is not None:
                    self._mode = "live"
                    self._message = "Связь есть"
                else:
                    self._message = "ИБП подключён по USB, жду нормальный ответ"
            if self._read_fails < 6:
                return
            self._read_fails = 0
            self._close_transport()
            self._event("warn", "link_lost", "Связь с ИБП прервалась, подключаюсь снова")
            return
        self._read_fails = 0
        if name == "Q1":
            self._handle_q1(reply)
        elif name in {"F", "I"}:
            self._handle_identity(name, reply)
        self._maybe_identity()

    def _handle_q1(self, raw: str) -> None:
        if "no ack" in raw.lower():
            self._note("←", raw)
            return
        try:
            status = parse_q1(raw)
        except ProtocolError as exc:
            self._note("!", str(exc))
            return
        if not plausible(status):
            self._note("!", "пустой кадр")
            return
        self._watch_freeze(raw)
        self._accept(raw, "live")

    def _watch_freeze(self, raw: str) -> None:
        """The bridge chip sometimes repeats one frame forever. Notice and recover."""
        if raw == self._last_raw:
            self._same_frames += 1
        else:
            self._same_frames = 0
            if self._frozen:
                self._frozen = False
                self._recover_stage = 0
                self._event("info", "thawed", "Данные с ИБП снова обновляются")
        self._last_raw = raw
        if self._same_frames < 20:
            return
        if not self._frozen:
            self._frozen = True
            self._frozen_since = time.time()
            self._event("warn", "frozen", "ИБП повторяет один и тот же кадр, оживляю связь")
        # Stage 0: the F/BL/I reads of the normal cycle get 20 more frames.
        # Stage 1: reopen the handle. Stage 2+: make Windows reset the chip.
        if self._same_frames == 40:
            self._recover_stage = 1
            self._close_transport()
        elif self._same_frames >= 70 and (self._same_frames - 70) % 40 == 0:
            self._recover_stage = 2
            self._event("warn", "reset", "Сбрасываю USB-мост ИБП, как это делает UPSmart")
            if self._transport is not None:
                self._transport.hard_reset()
            self._close_transport()

    def _handle_battery_level(self, reply: str) -> None:
        match = re.search(r"BL\s*(\d{1,3})", reply)
        if not match:
            return
        with self._lock:
            self._battery_level = min(100, int(match.group(1)))
            self._battery_level_at = time.time()

    def _handle_identity(self, name: str, raw: str) -> None:
        parser = parse_info if name == "I" else parse_ratings
        parsed = parser(raw)
        if not parsed:
            return
        with self._lock:
            if name == "I":
                if self._info != parsed:
                    self._note("←", raw)
                self._info = parsed
            else:
                self._ratings = parsed

    def _connect(self) -> None:
        busy = False
        last_error = ""
        devices = scan_hid()
        for device in devices:
            try:
                self._transport = open_mega(device)
                self._event("info", "link", f"Подключено: {self._transport.label}")
                with self._lock:
                    if self._reading is None:
                        self._mode = "searching"
                        self._message = "ИБП подключён по USB, читаю состояние"
                    else:
                        self._mode = "live"
                        self._message = "Связь есть"
                return
            except DeviceBusy:
                busy = True
            except (TransportError, ProtocolError, OSError) as exc:
                last_error = str(exc)
        if busy:
            who = "UPSmart" if upsmart_running() else "другая программа"
            with self._lock:
                self._mode = "blocked"
                self._message = f"ИБП найден, но порт занят программой {who}"
            return
        if devices:
            with self._lock:
                self._mode = "live" if self._reading is not None else "searching"
                self._message = "ИБП подключён по USB. Жду нормальный ответ."
            return
        with self._lock:
            preferred = self.settings.get("serial_port") or ""
        serial = None
        if preferred or time.time() - self._serial_checked > 30:
            self._serial_checked = time.time()
            try:
                serial = probe_serial(preferred)
            except (TransportError, OSError) as exc:
                last_error = str(exc)
        if serial is not None:
            self._transport = serial
            self._event("info", "link", f"Подключено: {serial.label}")
            with self._lock:
                self._mode = "live"
                self._message = "Связь есть"
            return
        with self._lock:
            self._mode = "missing"
            if upsmart_running():
                self._message = (
                    "Бесперебойник не найден. UPSmart сейчас запущен и может занимать порт. "
                    "Закройте его. Для работы UPS эта программа не нужна."
                )
            else:
                self._message = last_error or (
                    "Бесперебойник не найден. Проверьте USB-кабель. "
                    "Отдельная программа не нужна."
                )

    def _maybe_identity(self, force: bool = False) -> None:
        if self._transport is None or getattr(self._transport, "kind", "") == "demo":
            return
        if self._info and self._ratings and not force:
            return
        # I and F stall this USB chip if asked right after open. Wait until
        # Q1 has been flowing, then ask once in a while.
        if not force and (not self._live_since or time.time() - self._live_since < 45):
            return
        if not force and time.time() - self._info_at < 120:
            return
        self._info_at = time.time()
        for name, parser, slot in (
            ("I", parse_info, "info"),
            ("F", parse_ratings, "ratings"),
        ):
            try:
                raw = self._transport.command(name, retries=1)
            except (TransportError, OSError):
                continue
            self._note("→", name)
            self._note("←", raw)
            parsed = parser(raw)
            if parsed:
                with self._lock:
                    if slot == "info":
                        self._info = parsed
                    else:
                        self._ratings = parsed

    def _accept(self, raw: str, source: str) -> None:
        status = parse_q1(raw)
        now = time.time()
        with self._lock:
            settings = dict(self.settings)
        series = int(settings["battery_count"])
        groups = int(settings["battery_groups"])
        percent = battery_percent(
            status.battery_v,
            series,
            status.on_battery,
            float(settings["full_voltage"]),
            float(settings["eod_voltage"]),
        )
        if source == "live" and self._battery_level is not None and now - self._battery_level_at < 30:
            # The UPS reports its own charge level (BL command); trust it over the estimate.
            percent = float(self._battery_level)
        minutes = runtime_minutes(
            status.load_pct,
            float(settings["battery_ah"]) * groups,
            float(settings["ups_kva"]),
            float(settings["battery_health"]),
        )
        if status.on_battery:
            if self._outage_started is None:
                self._outage_started = now
            discharge = now - self._outage_started
        else:
            self._outage_started = None
            discharge = 0
            self._shutdown_held = False
        countdown = self._shutdown_countdown(status.on_battery, settings, now)
        loose = self._battery_loose(status.battery_v, source == "live")
        mode_changed = bool(self._edges.get("on_battery")) != bool(status.on_battery)
        swap = None if loose else self._note_pack_swap(source == "live", mode_changed)
        reading = status.as_dict()
        reading.update(
            {
                "battery_pct": None if percent is None else round(percent, 1),
                "runtime_min": minutes,
                "discharge_seconds": int(discharge),
                "shutdown_seconds": countdown,
                "mode_label": mode_label(status),
                "ups_kind": ups_kind(status),
                "mains_ok": not status.on_battery,
                "load_w": load_watts(status.load_pct, settings.get("ups_kva")),
                "battery_loose": loose,
                "battery_swap": swap,
                "voltage_state": voltage_state(reading.get("input_v"), status.on_battery),
                "updated_at": now,
                "source": source,
            }
        )
        point = {
            "t": now,
            "in": reading["input_v"],
            "out": reading["output_v"],
            "load": reading["load_pct"],
            "batt": reading["battery_v"],
            "soc": reading["battery_pct"],
            "hz": reading["frequency"],
            "temp": reading["temperature"],
        }
        with self._lock:
            self._reading = reading
            self._mode = source if source == "demo" else "live"
            if source == "live":
                self._message = "Связь есть"
                if not self._live_since:
                    self._live_since = now
            else:
                self._demo_points.append(point)
                self._demo_points = self._demo_points[-2000:]
            self._last_ok = now
        self._edges_from(reading)
        if source == "live":
            self._store(reading)

    def _shutdown_countdown(self, on_battery: bool, settings: dict, now: float) -> int | None:
        if not settings.get("pc_shutdown") or not on_battery or self._shutdown_held:
            if self._shutdown_sent:
                self._abort_os_shutdown()
            self._shutdown_at = None
            self._shutdown_sent = False
            return None
        if self._shutdown_at is None:
            self._shutdown_at = now + int(settings["shutdown_delay"])
            self._event("warn", "pc_shutdown", "Запущен отсчёт выключения компьютера")
        left = int(self._shutdown_at - now)
        if left <= 0 and not self._shutdown_sent:
            self._send_os_shutdown()
            return 0
        return max(0, left)

    def _send_os_shutdown(self) -> None:
        import subprocess

        self._shutdown_sent = True
        subprocess.Popen(
            ["shutdown", "/s", "/t", "20", "/c", f"{APP_NAME}: компьютер выключается, ИБП на батарее"],
            creationflags=CREATE_NO_WINDOW,
        )
        self._event("bad", "pc_off", "Компьютер выключится через 20 секунд")

    def _abort_os_shutdown(self) -> None:
        import subprocess

        if not self._shutdown_sent:
            return
        subprocess.Popen(["shutdown", "/a"], creationflags=CREATE_NO_WINDOW)
        self._shutdown_sent = False

    def _battery_loose(self, voltage: float | None, live: bool) -> bool:
        if not live or voltage is None:
            return bool(self._edges.get("battery_loose"))
        if self._live_since and time.time() - self._live_since < 10:
            return False
        self._batt_samples.append(float(voltage))
        self._batt_samples = self._batt_samples[-24:]
        return battery_voltage_chaotic(self._batt_samples)

    def _note_pack_swap(self, live: bool, mode_changed: bool) -> dict | None:
        now = time.time()
        if mode_changed:
            self._ignore_pack_until = now + 20
            return self._recent_swap(now)
        if not live or now < self._ignore_pack_until:
            return self._recent_swap(now)
        if self._live_since and now - self._live_since < 12:
            return self._recent_swap(now)
        step = battery_voltage_stepped(self._batt_samples)
        if step:
            old, new = step
            if self._pack_swap_to is None or abs(new - self._pack_swap_to) >= 0.8:
                self._pack_swap_to = new
                self._pack_swap = {"from": round(old, 1), "to": round(new, 1), "t": now}
                self._event(
                    "info",
                    "battery_swap",
                    f"Пакет батарей сменился: {old:.1f} → {new:.1f} В",
                )
        return self._recent_swap(now)

    def _recent_swap(self, now: float) -> dict | None:
        info = self._pack_swap
        if not info or now - float(info.get("t") or 0) > 300:
            return None
        return {"from": info.get("from"), "to": info.get("to")}

    def _edges_from(self, reading: dict) -> None:
        checks = (
            ("on_battery", reading["on_battery"], "bad", "Сеть пропала, питание от батареи", "info", "Сеть вернулась"),
            ("battery_low", reading["battery_low"], "bad", "Низкий заряд батареи", "info", "Заряд батареи снова в норме"),
            ("fault", reading["fault"], "bad", "ИБП сообщил об аварии", "info", "Авария снята"),
            ("battery_loose", reading.get("battery_loose"), "bad", "Напряжение батареи скачет — похоже, батареи отключены от ИБП", "info", "Напряжение батареи снова спокойное"),
            ("load_high", reading["load_pct"] >= 80, "warn", f"Высокая нагрузка: {reading['load_pct']:.0f}%", "info", "Нагрузка снизилась"),
        )
        for code, active, on_level, on_text, off_level, off_text in checks:
            previous = self._edges.get(code, False)
            self._edges[code] = active
            if active and not previous:
                self._event(on_level, code, on_text)
            elif previous and not active:
                self._event(off_level, code + "_clear", off_text)
        self._voltage_edges(reading)

    def _voltage_edges(self, reading: dict) -> None:
        volts = reading.get("input_v")
        on_battery = bool(reading.get("on_battery"))
        shown = "—" if volts is None else f"{float(volts):.1f}"
        checks = (
            ("voltage_crit_low", "bad", f"Входное напряжение опасно низкое: {shown} В", "info", "Напряжение уже не ниже 180 В"),
            ("voltage_low", "warn", f"Входное напряжение понижено: {shown} В", "info", "Напряжение снова выше 200 В"),
            ("voltage_high", "warn", f"Входное напряжение повышено: {shown} В", "info", "Напряжение снова ниже 240 В"),
            ("voltage_crit_high", "bad", f"Входное напряжение опасно высокое: {shown} В", "info", "Напряжение уже не выше 250 В"),
        )
        for code, on_level, on_text, off_level, off_text in checks:
            previous = bool(self._edges.get(code))
            active = mains_voltage_active(code, volts, on_battery, previous)
            self._edges[code] = active
            if active and not previous:
                self._event(on_level, code, on_text)
            elif previous and not active and mains_present(volts, on_battery):
                self._event(off_level, code + "_clear", off_text)

    def _enter_demo(self) -> None:
        self._demo_forced = True
        self._close_transport()
        self._demo = DemoSource()
        self._demo_points = _seed_demo()
        with self._lock:
            self._mode = "demo"
            self._message = "Демонстрация. Это не данные вашего ИБП."
            self._reading = None

    def _close_transport(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._live_since = 0.0
        self._batt_samples = []
        self._pack_swap = None
        self._pack_swap_to = None
        self._ignore_pack_until = 0.0

    def _note(self, direction: str, text: str) -> None:
        line = {"t": time.time(), "dir": direction, "text": " ".join(str(text).split())[:180]}
        with self._lock:
            self._trace.append(line)
            self._trace = self._trace[-40:]

    def _event(self, level: str, code: str, message: str) -> None:
        item = {"t": time.time(), "level": level, "code": code, "message": message}
        with self._lock:
            if self._events and self._events[-1]["code"] == code and item["t"] - self._events[-1]["t"] < 20:
                return
            self._events.append(item)
            self._events = self._events[-500:]
            reading = None if self._reading is None else dict(self._reading)
        self._db.execute(
            "INSERT INTO events (ts, level, code, message) VALUES (?, ?, ?, ?)",
            (item["t"], level, code, message),
        )
        self._db.commit()
        self._telegram.notify(item, reading)
        if item.get("code") in reports.IMPORTANT:
            self._journal_at = 0.0
            self._flush_journal()

    def _store(self, reading: dict) -> None:
        self._db.execute(
            """
            INSERT OR REPLACE INTO readings (
                ts, input_v, fault_v, output_v, load_pct, frequency, battery_v,
                battery_pct, temperature, on_battery, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading["updated_at"],
                reading["input_v"],
                reading["fault_v"],
                reading["output_v"],
                reading["load_pct"],
                reading["frequency"],
                reading["battery_v"],
                reading["battery_pct"],
                reading["temperature"],
                1 if reading["on_battery"] else 0,
                "live",
            ),
        )
        self._db.commit()
        self._flush_journal()

    def _flush_journal(self, force: bool = False) -> None:
        with self._lock:
            settings = dict(self.settings)
        if not settings.get("journal_enabled", True):
            return
        now = time.time()
        stamp = reports.today_stamp()
        if not force and stamp == self._journal_day and now - self._journal_at < 60:
            return
        if self._journal_day and self._journal_day != stamp:
            try:
                previous = reports.build_day(self._db, self._journal_day)
                root = reports.resolve_journal_dir(settings.get("journal_dir"))
                reports.write_day(root, previous)
                reports.write_summary(root, reports.build_summary(root, previous))
            except OSError:
                pass
        self._journal_day = stamp
        self._journal_at = now
        try:
            day = reports.build_day(self._db, stamp)
            root = reports.resolve_journal_dir(settings.get("journal_dir"))
            reports.write_day(root, day)
            reports.write_summary(root, reports.build_summary(root, day))
        except OSError:
            return

    def _day_report(self, stamp: str | None = None) -> dict:
        day = reports.parse_day(stamp) or reports.today_stamp()
        with self._lock:
            root = reports.resolve_journal_dir(self.settings.get("journal_dir"))
        stored = reports.load_day(root, day)
        built = reports.build_day(self._db, day)
        if stored and len(stored.get("points") or []) >= len(built.get("points") or []):
            if len(stored.get("events") or []) >= len(built.get("events") or []):
                return stored
        return built

    def _summary_report(self) -> dict:
        with self._lock:
            root = reports.resolve_journal_dir(self.settings.get("journal_dir"))
            enabled = bool(self.settings.get("journal_enabled", True))
        extra = None
        try:
            extra = reports.build_day(self._db, reports.today_stamp())
        except sqlite3.Error:
            extra = None
        if enabled:
            try:
                reports.ensure_journal(root)
                if extra:
                    reports.write_day(root, extra)
            except OSError:
                pass
        return reports.build_summary(root, extra)

    def _init_db(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS readings (
                ts REAL PRIMARY KEY,
                input_v REAL,
                fault_v REAL,
                output_v REAL,
                load_pct REAL,
                frequency REAL,
                battery_v REAL,
                battery_pct REAL,
                temperature REAL,
                on_battery INTEGER,
                source TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL,
                level TEXT,
                code TEXT,
                message TEXT
            );
            """
        )
        self._db.execute("DELETE FROM readings WHERE ts < ?", (time.time() - 30 * 86400,))
        self._db.commit()

    def _load_events(self) -> None:
        rows = self._db.execute(
            "SELECT ts, level, code, message FROM events ORDER BY id DESC LIMIT 200"
        ).fetchall()
        self._events = [
            {"t": row[0], "level": row[1], "code": row[2], "message": row[3]}
            for row in reversed(rows)
        ]


CREATE_NO_WINDOW = 0x08000000


def _seed_demo() -> list[dict]:
    points = []
    now = time.time()
    for index in range(180):
        t = now - (180 - index) * 5
        wave = ((index % 40) - 20) * 0.08
        points.append(
            {
                "t": t,
                "in": round(216 + wave, 1),
                "out": round(215 + wave * 0.4, 1),
                "load": 45,
                "batt": 26.6,
                "soc": 100,
                "hz": 49.6,
                "temp": 29.0,
            }
        )
    return points


def _sane_point(point: dict) -> bool:
    battery = point.get("batt") or 0
    incoming = point.get("in") or 0
    return not (battery < 5 and incoming < 20)


def _decimate(points: list[dict], limit: int) -> list[dict]:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit)]


def _command_result(action: str, reply: str) -> str:
    if action == "test":
        return "Команда теста отправлена"
    if action == "cancel_test":
        return "Отмена теста отправлена"
    if action == "beeper":
        return "Команда зуммера отправлена"
    return _command_hint(action)


def _command_hint(action: str) -> str:
    return {
        "rescan": "Ищу устройство заново",
        "test": "Запрошен тест батареи на 10 секунд",
        "cancel_test": "Тест отменён",
        "beeper": "Сигнал ИБП переключён",
        "demo": "Открыта демонстрация",
        "live": "Возвращаюсь к настоящему ИБП",
        "simulate": "Имитация сети переключена",
        "cancel_shutdown": "Выключение компьютера отменено",
    }.get(action, action)


def _validate(payload: dict) -> tuple[dict, str | None]:
    if not isinstance(payload, dict):
        return {}, "Нужен объект с настройками"
    clean = {}
    for key, value in payload.items():
        if key == "serial_port":
            clean[key] = str(value or "")[:40]
            continue
        if key == "telegram_token":
            token = str(value or "").strip()
            if token and not TOKEN_RE.fullmatch(token):
                return {}, "Токен бота не похож на настоящий"
            clean[key] = token
            continue
        if key in {
            "sound", "pc_shutdown", "telegram_notify_power", "telegram_notify_alerts",
            "telegram_digest", "autostart", "journal_enabled",
        }:
            clean[key] = bool(value)
            continue
        if key == "journal_dir":
            text = str(value or "").strip()
            if "\x00" in text or len(text) > 240:
                return {}, "Путь к журналу слишком длинный"
            clean[key] = str(reports.resolve_journal_dir(text))
            continue
        if key == "telegram_digest_time":
            clock = parse_clock(value)
            if not clock:
                return {}, "Время отчёта должно быть в формате ЧЧ:ММ"
            clean[key] = clock
            continue
        if key not in _LIMITS:
            continue
        low, high = _LIMITS[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            return {}, f"Поле «{key}» должно быть числом"
        if key in {"poll_seconds", "battery_count", "battery_groups", "battery_health", "shutdown_delay"}:
            number = int(number)
        if not low <= number <= high:
            return {}, f"Поле «{key}» вне допустимых пределов"
        clean[key] = number
    full = clean.get("full_voltage", DEFAULTS["full_voltage"])
    eod = clean.get("eod_voltage", DEFAULTS["eod_voltage"])
    if eod >= full:
        return {}, "Конец разряда должен быть ниже напряжения полного заряда"
    return clean, None


def _load_settings() -> dict:
    path = DATA / "settings.json"
    settings = dict(DEFAULTS)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if isinstance(stored, dict):
            settings.update({key: stored[key] for key in DEFAULTS if key in stored})
    return settings


def _save_settings(settings: dict) -> None:
    path = DATA / "settings.json"
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _public_settings(settings: dict) -> dict:
    data = dict(settings)
    data["telegram_token_set"] = bool(data.get("telegram_token"))
    data["telegram_token"] = ""
    data["telegram_chats"] = [
        {"id": item.get("id"), "name": item.get("name") or ""}
        for item in data.get("telegram_chats") or []
        if isinstance(item, dict)
    ]
    data["autostart"] = autostart.is_enabled()
    return data
