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
    battery_low_active,
    battery_percent,
    battery_voltage_chaotic,
    battery_voltage_stepped,
    CHARGE_HOLD,
    charge_recovered,
    pack_voltage_unsettled,
    load_watts,
    mains_present,
    mains_voltage_active,
    battery_boost_active,
    stabilizer_dir,
    stabilizer_edge,
    mode_label,
    pack_capacity_wh,
    pack_energy_wh,
    voltage_limits,
    voltage_note,
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
    hid_still_there,
    list_serial_ports,
    open_mega,
    open_serial,
    probe_serial,
    scan_hid,
    upsmart_running,
    _likely_serial,
)
from ups import reports
from ups.notify_art import card_title, notify_image
from ups.telegram import (
    TOKEN_RE,
    TelegramBridge,
    clean_guest_actions,
    clean_guest_events,
    default_event_sound,
    default_event_tray,
    default_guest_actions,
    default_guest_events,
    format_events,
    format_report,
    merge_global_events,
    parse_clock,
    public_chat,
    status_lines,
    windows_notify_allowed,
)

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
    "low_voltage": 11.1,
    "q1_battery_low": False,
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
    "telegram_events": {},
    "telegram_digest": False,
    "telegram_digest_time": "21:00",
    "windows_notify": True,
    "spark_lines": ["in", "out", "batt"],
    "spark_window": 900,
    "chart_minutes": 60,
    "chart_series": "voltage",
    "journal_enabled": True,
    "journal_dir": "",
    "volt_low": 200,
    "volt_high": 240,
    "volt_crit_low": 180,
    "volt_crit_high": 250,
    "window_x": None,
    "window_y": None,
    "window_width": 1320,
    "window_height": 860,
    "window_maximized": False,
}

_LIMITS = {
    "poll_seconds": (1, 30),
    "battery_count": (1, 8),
    "battery_groups": (1, 6),
    "battery_ah": (1, 300),
    "ups_kva": (0.2, 20),
    "full_voltage": (12, 15),
    "eod_voltage": (9, 13),
    "low_voltage": (9.5, 13.5),
    "battery_health": (10, 100),
    "shutdown_delay": (30, 3600),
    "volt_low": (80, 300),
    "volt_high": (80, 300),
    "volt_crit_low": (80, 300),
    "volt_crit_high": (80, 300),
    "spark_window": (30, 86400),
}

SIMULATIONS = (
    {"code": "on_battery", "level": "warn", "group": "Сеть", "title": "Сеть пропала",
     "message": "Симуляция · сеть пропала. ИБП и компьютер не затронуты."},
    {"code": "on_battery_clear", "level": "ok", "group": "Сеть", "title": "Сеть вернулась",
     "message": "Симуляция · сеть вернулась."},
    {"code": "voltage_low", "level": "warn", "group": "Сеть", "title": "Сеть понижена",
     "message": "Симуляция · вход ниже порога предупреждения."},
    {"code": "voltage_low_clear", "level": "ok", "group": "Сеть", "title": "Понижение снято",
     "message": "Симуляция · напряжение сети снова в норме."},
    {"code": "voltage_high", "level": "warn", "group": "Сеть", "title": "Сеть повышена",
     "message": "Симуляция · вход выше порога предупреждения."},
    {"code": "voltage_high_clear", "level": "ok", "group": "Сеть", "title": "Повышение снято",
     "message": "Симуляция · напряжение сети снова в норме."},
    {"code": "voltage_crit_low", "level": "bad", "group": "Сеть", "title": "Сеть слишком низкая",
     "message": "Симуляция · вход ниже критического порога."},
    {"code": "voltage_crit_low_clear", "level": "ok", "group": "Сеть", "title": "Спад снят",
     "message": "Симуляция · критически низкое напряжение снято."},
    {"code": "voltage_crit_high", "level": "bad", "group": "Сеть", "title": "Сеть слишком высокая",
     "message": "Симуляция · вход выше критического порога."},
    {"code": "voltage_crit_high_clear", "level": "ok", "group": "Сеть", "title": "Скачок снят",
     "message": "Симуляция · критически высокое напряжение снято."},
    {"code": "battery_boost", "level": "warn", "group": "Сеть", "title": "Стабилизатор",
     "message": "Симуляция · стабилизатор поднял выход больше чем на 20 В относительно входа."},
    {"code": "battery_boost_clear", "level": "ok", "group": "Сеть", "title": "Стабилизатор отпустил",
     "message": "Симуляция · выход снова близок ко входу."},
    {"code": "battery_buck", "level": "warn", "group": "Сеть", "title": "Стабилизатор (понижение)",
     "message": "Симуляция · стабилизатор понизил выход больше чем на 20 В относительно входа."},
    {"code": "battery_buck_clear", "level": "ok", "group": "Сеть", "title": "Стабилизатор отпустил",
     "message": "Симуляция · выход снова близок ко входу."},
    {"code": "battery_low", "level": "warn", "group": "Батарея", "title": "Батарея садится",
     "message": "Симуляция · низкий заряд батареи."},
    {"code": "battery_low_clear", "level": "ok", "group": "Батарея", "title": "Батарея в норме",
     "message": "Симуляция · батарея заряжена до 100%."},
    {"code": "battery_loose", "level": "bad", "group": "Батарея", "title": "Батареи отключены",
     "message": "Симуляция · напряжение пакета скачет, как при обрыве."},
    {"code": "battery_loose_clear", "level": "ok", "group": "Батарея", "title": "Пакет в норме",
     "message": "Симуляция · пакет батарей снова на месте."},
    {"code": "battery_swap", "level": "warn", "group": "Батарея", "title": "Пакет сменился",
     "message": "Симуляция · пакет батарей сменился на другой."},
    {"code": "fault", "level": "bad", "group": "ИБП", "title": "Неисправность",
     "message": "Симуляция · ИБП сообщил об аварии."},
    {"code": "fault_clear", "level": "ok", "group": "ИБП", "title": "Авария снята",
     "message": "Симуляция · авария ИБП снята."},
    {"code": "link_lost", "level": "warn", "group": "ИБП", "title": "Нет связи с ИБП",
     "message": "Симуляция · связь с ИБП потеряна."},
    {"code": "link", "level": "ok", "group": "ИБП", "title": "ИБП на связи",
     "message": "Симуляция · USB снова подключён, связь с ИБП есть."},
    {"code": "pc_shutdown", "level": "warn", "group": "ПК", "title": "Отсчёт выключения",
     "message": "Симуляция · запущен отсчёт выключения ПК. Питание не трогаем."},
    {"code": "pc_off", "level": "bad", "group": "ПК", "title": "Выключить ПК",
     "message": "Симуляция · команда выключения ПК. Компьютер не выключится."},
    {"code": "pc_reboot", "level": "warn", "group": "ПК", "title": "Перезагрузка",
     "message": "Симуляция · команда перезагрузки. Компьютер не перезагрузится."},
    {"code": "pc_sleep", "level": "warn", "group": "ПК", "title": "Сон",
     "message": "Симуляция · команда сна. Компьютер не уснёт."},
    {"code": "pc_hibernate", "level": "warn", "group": "ПК", "title": "Гибернация",
     "message": "Симуляция · команда гибернации. Компьютер не уйдёт в гибернацию."},
    {"code": "startup", "level": "ok", "group": "Программа", "title": "UPS запущен",
     "message": "Симуляция · программа запущена."},
    {"code": "shutdown", "level": "info", "group": "Программа", "title": "UPS закрыт",
     "message": "Симуляция · программа закрыта. Процесс не завершается."},
    {"code": "app_restart", "level": "warn", "group": "Программа", "title": "Перезапуск UPS",
     "message": "Симуляция · перезапуск программы. start.bat не запускается."},
)


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
        self._charge_armed = False
        self._charge_full_since = 0.0
        self._outage_started: float | None = None
        self._shutdown_at: float | None = None
        self._shutdown_sent = False
        self._shutdown_held = False
        self._shutdown_kind: str | None = None
        self._session_started = time.time()
        self._last_ok = 0.0
        self._live_since = 0.0
        self._read_fails = 0
        self._hid_misses = 0
        self._need_link_notice = False
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
        self._tray = None
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

    def set_tray(self, tray) -> None:
        self._tray = tray

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
                chats.append({"id": chat_id, "name": str(chat.get("name") or "")[:80], "guest": False})
            self.settings["telegram_chats"] = chats
            _save_settings(self.settings)

    def update_telegram_chat(self, payload: dict) -> dict:
        action = str((payload or {}).get("action") or "update")
        try:
            chat_id = int((payload or {}).get("id"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "Нет id чата"}
        actor_id = (payload or {}).get("actor")
        with self._lock:
            chats = [item for item in self.settings.get("telegram_chats") or [] if isinstance(item, dict)]
            if actor_id is not None:
                try:
                    actor = next(item for item in chats if int(item.get("id") or 0) == int(actor_id))
                except (StopIteration, TypeError, ValueError):
                    return {"ok": False, "message": "Нет прав админа"}
                if not actor.get("admin") or actor.get("guest"):
                    return {"ok": False, "message": "Нет прав админа"}
            if actor_id is not None and int(actor_id) == chat_id:
                if action == "unlink" or payload.get("guest") or ("admin" in payload and not payload.get("admin")):
                    return {"ok": False, "message": "Админ не может снять с себя права"}
            if action == "unlink":
                chats = [item for item in chats if int(item.get("id") or 0) != chat_id]
                self.settings["telegram_chats"] = chats
                _save_settings(self.settings)
                return {"ok": True, "message": "Чат отвязан", "settings": _public_settings(self.settings)}
            found = None
            for item in chats:
                if int(item.get("id") or 0) == chat_id:
                    found = item
                    break
            if found is None:
                return {"ok": False, "message": "Чат не найден"}
            if payload.get("admin"):
                found["admin"] = True
                found["guest"] = False
            elif "guest" in payload:
                found["guest"] = bool(payload.get("guest"))
                if found["guest"]:
                    found["admin"] = False
            if "admin" in payload and not payload.get("admin") and not found.get("guest"):
                found["admin"] = False
            if found.get("admin") and not found.get("guest"):
                found.pop("events", None)
                found.pop("actions", None)
            else:
                if "events" in payload:
                    found["events"] = clean_guest_events(payload.get("events"))
                elif found.get("guest") and not isinstance(found.get("events"), dict):
                    found["events"] = default_guest_events(self.settings)
                else:
                    found.pop("events", None)
                if "actions" in payload:
                    found["actions"] = clean_guest_actions(payload.get("actions"))
                elif found.get("guest") and not isinstance(found.get("actions"), dict):
                    found["actions"] = default_guest_actions()
                else:
                    found.pop("actions", None)
            self.settings["telegram_chats"] = chats
            _save_settings(self.settings)
            snapshot = _public_settings(self.settings)
        return {"ok": True, "message": "Правила чата сохранены", "settings": snapshot, "chat": next((item for item in snapshot.get("telegram_chats") or [] if int(item.get("id") or 0) == chat_id), None)}

    def update_global_telegram_events(self, patch, actor_id=None) -> dict:
        with self._lock:
            if actor_id is not None:
                chats = [item for item in self.settings.get("telegram_chats") or [] if isinstance(item, dict)]
                try:
                    actor = next(item for item in chats if int(item.get("id") or 0) == int(actor_id))
                except (StopIteration, TypeError, ValueError):
                    return {"ok": False, "message": "Нет прав админа"}
                if not actor.get("admin") or actor.get("guest"):
                    return {"ok": False, "message": "Нет прав админа"}
            self.settings["telegram_events"] = merge_global_events(self.settings, patch)
            _save_settings(self.settings)
            snapshot = dict(self.settings)
        self._telegram.configure(snapshot)
        return {"ok": True, "message": "Общие уведомления сохранены", "settings": _public_settings(snapshot)}

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

    def energy(self, start: float, end: float) -> dict:
        now = time.time()
        try:
            end_ts = float(end) if end else now
        except (TypeError, ValueError):
            end_ts = now
        try:
            start_ts = float(start) if start else end_ts - 86400
        except (TypeError, ValueError):
            start_ts = end_ts - 86400
        end_ts = min(end_ts, now + 60)
        if end_ts <= start_ts:
            start_ts = end_ts - 3600
        max_span = 90 * 86400
        if end_ts - start_ts > max_span:
            start_ts = end_ts - max_span
        kva = self._kva()
        with self._lock:
            mode = self._mode
            demo = list(self._demo_points) if mode == "demo" else []
        if mode == "demo":
            rows = [
                (point["t"], point.get("load"), 1 if point.get("on") else 0)
                for point in demo
                if start_ts <= point["t"] < end_ts
            ]
            return reports.energy_report(rows, kva, start_ts, end_ts)
        return reports.energy_from_db(self._db, kva, start_ts, end_ts)

    def _kva(self) -> float:
        with self._lock:
            return reports.normalize_kva(self.settings.get("ups_kva"))

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

    def scan_ports(self) -> dict:
        current_path = ""
        current_com = ""
        transport = self._transport
        if transport is not None:
            current_path = str(getattr(getattr(transport, "found", None), "path", "") or "")
            current_com = str(getattr(transport, "port", "") or "")
        hid = []
        for device in scan_hid():
            item = {
                "kind": "hid",
                "id": f"{device.vendor_id:04X}:{device.product_id:04X}",
                "path": device.path,
                "label": f"Mega USB {device.vendor_id:04X}:{device.product_id:04X}",
                "status": "найден",
            }
            if current_path and device.path.lower() == current_path.lower():
                item["status"] = "подключено сейчас"
                hid.append(item)
                continue
            try:
                probe = open_mega(device)
                try:
                    probe.query_q1()
                    item["status"] = "отвечает"
                except (TransportError, ProtocolError, OSError) as exc:
                    item["status"] = f"открыт, нет ответа: {exc}"
                finally:
                    probe.close()
            except DeviceBusy:
                item["status"] = "занят другой программой"
            except (TransportError, OSError) as exc:
                item["status"] = str(exc)
            hid.append(item)
        com = []
        ports = list_serial_ports()
        for port in ports:
            device = str(port.get("device") or "")
            desc = str(port.get("description") or "")
            hwid = str(port.get("hwid") or "")
            likely = _likely_serial(desc, hwid)
            item = {
                "kind": "com",
                "device": device,
                "description": desc,
                "likely": likely,
                "status": "похоже на ИБП" if likely else "обычный порт",
            }
            if current_com and device.upper() == current_com.upper():
                item["status"] = "подключено сейчас"
                com.append(item)
                continue
            if likely:
                try:
                    probe = open_serial(device)
                    probe.close()
                    item["status"] = "отвечает как Megatec"
                except (TransportError, OSError) as exc:
                    item["status"] = f"не ответил: {exc}"
            com.append(item)
        with self._lock:
            self._ports = ports
            self._ports_at = time.time()
        hid_n = len(hid)
        com_n = len(com)
        return {
            "ok": True,
            "hid": hid,
            "com": com,
            "message": f"HID {hid_n}, COM {com_n}",
        }

    def state(self) -> dict:
        if self._mode == "blocked":
            self._blocker_cached()
        if time.time() - self._ports_at > 30:
            self._ports = list_serial_ports()
            self._ports_at = time.time()
        with self._lock:
            reading = None if self._reading is None else dict(self._reading)
            if reading is not None:
                reading["shutdown_seconds"] = self._visible_countdown()
                reading["shutdown_kind"] = self._shutdown_kind
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
                "events": list(reversed(self._events[-8:])),
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
        span_min = float(minutes)
        if span_min <= 20:
            limit = 2400
        elif span_min <= 90:
            limit = 1600
        elif span_min <= 400:
            limit = 1100
        else:
            limit = 800
        return {"points": _decimate([point for point in points if _sane_point(point)], limit)}

    def simulations(self) -> dict:
        return {"ok": True, "items": [dict(item) for item in SIMULATIONS]}

    def simulate_event(self, code: str) -> dict:
        spec = next((item for item in SIMULATIONS if item["code"] == code), None)
        if spec is None:
            return {"ok": False, "message": "Нет такой симуляции"}
        self._event(spec["level"], spec["code"], spec["message"], force=True)
        return {"ok": True, "message": f"Симуляция: {spec['title']}"}

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
                    wait = 2 if self._mode == "blocked" else 1.5
                elif self._mode == "searching" and self._transport is None:
                    wait = 1.0
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
            self._clear_shutdown()
            self._stamp_reading_countdown()
            self._event("info", "shutdown_cancel", "Выключение компьютера отменено")
            return {"ok": True, "message": "Выключение компьютера отменено"}
        if action == "pc_off":
            power.shutdown(20, f"{APP_NAME}: выключение с телефона")
            self._arm_power("off", 20)
            self._event("bad", "pc_off", "Компьютер выключится через 20 секунд")
            return {"ok": True, "message": "Компьютер выключится через 20 секунд. Можно отменить."}
        if action == "pc_reboot":
            power.restart(20, f"{APP_NAME}: перезагрузка с телефона")
            self._arm_power("reboot", 20)
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

    def _hid_unplugged(self) -> bool:
        transport = self._transport
        if transport is None or getattr(transport, "kind", "") != "mega-usb":
            self._hid_misses = 0
            return False
        path = getattr(getattr(transport, "found", None), "path", "")
        if not path or hid_still_there(path):
            self._hid_misses = 0
            return False
        self._hid_misses += 1
        return self._hid_misses >= 2

    def _lose_link(self, message: str) -> None:
        had = self._transport is not None or self._reading is not None
        self._close_transport()
        self._read_fails = 0
        self._hid_misses = 0
        with self._lock:
            self._reading = None
            self._mode = "searching"
            self._message = message
        if had:
            self._need_link_notice = True
            self._event("warn", "link_lost", "Связь с ИБП прервалась, ищу снова")

    def _announce_link(self) -> None:
        if not self._need_link_notice:
            return
        self._need_link_notice = False
        label = getattr(self._transport, "label", "") if self._transport else ""
        text = "ИБП снова на связи"
        if label:
            text = f"{text} · {label}"
        self._event("ok", "link", text)

    def _poll_demo(self) -> None:
        raw = self._demo.query_q1()
        self._accept(raw, "demo")

    def _poll_live(self) -> None:
        if self._transport is not None and self._hid_unplugged():
            self._lose_link("USB отключили, ищу бесперебойник")
        if self._transport is None:
            self._connect()
            if self._transport is None:
                return
        try:
            name, reply = self._transport.poll_step()
        except (TransportError, ProtocolError, OSError) as exc:
            self._read_fails += 1
            self._note("!", str(exc))
            path = getattr(getattr(self._transport, "found", None), "path", "")
            yanked = getattr(self._transport, "kind", "") == "mega-usb" and path and not hid_still_there(path)
            if yanked or self._read_fails >= 3:
                self._lose_link("Связь с ИБП пропала, ищу снова")
                return
            with self._lock:
                self._message = "Нет ответа от ИБП, пробую ещё"
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
                self._hid_misses = 0
                try:
                    self._handle_q1(self._transport.query_q1())
                except (TransportError, ProtocolError, OSError):
                    pass
                with self._lock:
                    if self._reading is not None:
                        self._mode = "live"
                        self._message = "Связь есть"
                    else:
                        self._mode = "searching"
                        self._message = "ИБП подключён по USB, читаю состояние"
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
        try:
            serial = probe_serial(preferred)
        except (TransportError, OSError) as exc:
            last_error = last_error or str(exc)
        if serial is not None:
            self._transport = serial
            if serial.port and serial.port != preferred:
                with self._lock:
                    self.settings["serial_port"] = serial.port
                    _save_settings(self.settings)
            with self._lock:
                self._mode = "live"
                self._message = "Связь есть"
            return
        hid_n = len(devices)
        coms = ", ".join(item["device"] for item in list_serial_ports()) or "нет"
        with self._lock:
            self._mode = "missing"
            if upsmart_running():
                self._message = (
                    "Бесперебойник не найден. UPSmart сейчас запущен и может занимать порт. "
                    "Закройте его. Для работы UPS эта программа не нужна."
                )
            else:
                self._message = last_error or (
                    "Бесперебойник не найден. После переподключения порт мог смениться. "
                    f"Сейчас HID {hid_n}, COM: {coms}."
                )
        if time.time() - self._session_started > 2:
            self._need_link_notice = True

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
        mode_changed = bool(self._edges.get("on_battery")) != bool(status.on_battery)
        was_boost = bool(self._edges.get("battery_boost"))
        boosting = battery_boost_active(
            status.input_v,
            status.output_v,
            status.on_battery,
            was_boost,
        )
        boost_dir = stabilizer_dir(
            status.input_v,
            status.output_v,
            status.on_battery,
            was_boost,
        )
        if mode_changed or stabilizer_edge(
            was_boost, boosting, self._edges.get("stabilizer_dir"), boost_dir
        ):
            self._arm_pack_quiet(now)
        loose = self._battery_loose(status.battery_v, source == "live")
        swap = None if loose else self._note_pack_swap(source == "live")
        reading = status.as_dict()
        volt = voltage_state(reading.get("input_v"), status.on_battery, settings)
        capacity_wh = pack_capacity_wh(
            series,
            groups,
            float(settings["battery_ah"]),
            float(settings["battery_health"]),
        )
        reading.update(
            {
                "battery_low": False if loose else self._resolve_battery_low(status, settings, now),
                "battery_pct": None if percent is None else round(percent, 1),
                "battery_wh": pack_energy_wh(percent, capacity_wh),
                "battery_wh_full": capacity_wh,
                "runtime_min": minutes,
                "discharge_seconds": int(discharge),
                "shutdown_seconds": countdown,
                "shutdown_kind": self._shutdown_kind,
                "mode_label": mode_label(status),
                "ups_kind": ups_kind(status),
                "mains_ok": not status.on_battery,
                "load_w": load_watts(status.load_pct, settings.get("ups_kva")),
                "battery_loose": loose,
                "battery_swap": swap,
                "voltage_state": volt,
                "voltage_note": voltage_note(volt, settings),
                "volt_limits": voltage_limits(settings),
                "battery_boost": boosting,
                "stabilizer_dir": boost_dir,
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
            "boost": bool(reading.get("battery_boost")),
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
            self._announce_link()

    def _visible_countdown(self, now: float | None = None) -> int | None:
        if self._shutdown_held or self._shutdown_at is None:
            return None
        return max(0, int(self._shutdown_at - (now if now is not None else time.time())))

    def _clear_shutdown(self) -> None:
        self._shutdown_at = None
        self._shutdown_sent = False
        self._shutdown_kind = None

    def _arm_power(self, kind: str, seconds: int) -> None:
        self._shutdown_held = False
        self._shutdown_kind = kind
        self._shutdown_at = time.time() + int(seconds)
        self._shutdown_sent = True
        self._stamp_reading_countdown()

    def _stamp_reading_countdown(self) -> None:
        with self._lock:
            if self._reading is None:
                return
            self._reading["shutdown_seconds"] = self._visible_countdown()
            self._reading["shutdown_kind"] = self._shutdown_kind

    def _shutdown_countdown(self, on_battery: bool, settings: dict, now: float) -> int | None:
        if self._shutdown_held:
            if self._shutdown_sent:
                self._abort_os_shutdown()
            self._clear_shutdown()
            return None
        if self._shutdown_kind in {"off", "reboot"}:
            return self._visible_countdown(now)
        if not settings.get("pc_shutdown") or not on_battery:
            if self._shutdown_sent:
                self._abort_os_shutdown()
            self._clear_shutdown()
            return None
        if self._shutdown_at is None:
            self._shutdown_kind = "battery"
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

    def _resolve_battery_low(self, status, settings: dict, now: float) -> bool:
        if settings.get("q1_battery_low", False):
            return bool(status.battery_low)
        was = bool(self._edges.get("battery_low"))
        if now < self._ignore_pack_until and status.on_battery:
            return was
        return battery_low_active(
            status.battery_v,
            int(settings["battery_count"]),
            status.on_battery,
            float(settings.get("low_voltage", 11.1)),
            was,
        )

    def _arm_pack_quiet(self, now: float) -> None:
        # Mains↔battery and AVR tap changes both kick pack voltage.
        # That is not a swap and not a disconnected string.
        self._ignore_pack_until = now + 45
        self._batt_samples = []

    def _battery_loose(self, voltage: float | None, live: bool) -> bool:
        if not live or voltage is None:
            return bool(self._edges.get("battery_loose"))
        now = time.time()
        if self._live_since and now - self._live_since < 10:
            return False
        if now < self._ignore_pack_until:
            return bool(self._edges.get("battery_loose"))
        self._batt_samples.append(float(voltage))
        self._batt_samples = self._batt_samples[-24:]
        return battery_voltage_chaotic(self._batt_samples)

    def _note_pack_swap(self, live: bool) -> dict | None:
        now = time.time()
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
            ("battery_low", reading["battery_low"], "bad", "Низкий заряд батареи", None, None),
            ("fault", reading["fault"], "bad", "ИБП сообщил об аварии", "info", "Авария снята"),
            ("battery_loose", reading.get("battery_loose"), "bad", "Напряжение батареи скачет — похоже, батареи отключены от ИБП", "info", "Напряжение батареи снова спокойное"),
            ("load_high", reading["load_pct"] >= 80, "warn", f"Высокая нагрузка: {reading['load_pct']:.0f}%", "info", "Нагрузка снизилась"),
        )
        for code, active, on_level, on_text, off_level, off_text in checks:
            previous = self._edges.get(code, False)
            self._edges[code] = active
            if active and not previous:
                self._event(on_level, code, on_text)
            elif previous and not active and off_text:
                self._event(off_level, code + "_clear", off_text)
        self._voltage_edges(reading)
        self._boost_edges(reading)
        self._charge_full_edges(reading)

    def _charge_full_edges(self, reading: dict) -> None:
        now = float(reading.get("updated_at") or time.time())
        quiet = now < self._ignore_pack_until
        loose = bool(reading.get("battery_loose"))
        pulled = loose or self._recent_pack_removed(now)
        armed, ready = charge_recovered(
            reading.get("battery_pct"),
            bool(reading.get("on_battery")),
            self._charge_armed,
            quiet,
            loose=pulled,
        )
        self._charge_armed = armed
        if pulled or not ready or pack_voltage_unsettled(self._batt_samples):
            self._charge_full_since = 0.0
            return
        if self._charge_full_since <= 0:
            self._charge_full_since = now
            return
        if now - self._charge_full_since < CHARGE_HOLD:
            return
        self._charge_full_since = 0.0
        self._charge_armed = False
        pct = reading.get("battery_pct")
        text = "Батарея заряжена · 100%" if pct is None else f"Батарея заряжена · {pct:.0f}%"
        self._event("ok", "battery_low_clear", text)

    def _recent_pack_removed(self, now: float) -> bool:
        window = CHARGE_HOLD + 2.0
        with self._lock:
            items = list(self._events[-20:])
        for item in reversed(items):
            if now - float(item.get("t") or 0) > window:
                break
            if item.get("code") == "battery_loose":
                return True
        return False

    def _boost_edges(self, reading: dict) -> None:
        previous = bool(self._edges.get("battery_boost"))
        prev_dir = self._edges.get("stabilizer_dir")
        active = bool(reading.get("battery_boost"))
        direction = reading.get("stabilizer_dir")
        self._edges["battery_boost"] = active
        self._edges["stabilizer_dir"] = direction
        if active and (not previous or (prev_dir and direction and prev_dir != direction)):
            self._event("warn", "battery_boost", self._boost_text(reading))
        elif previous and not active:
            self._event("info", "battery_boost_clear", "Выход снова близок ко входу — стабилизатор отпустил")

    def _boost_text(self, reading: dict) -> str:
        inn = reading.get("input_v")
        out = reading.get("output_v")
        try:
            delta = float(out) - float(inn)
        except (TypeError, ValueError):
            return "Сработал стабилизатор напряжения"
        mag = abs(delta)
        if delta < 0:
            return f"Вход выше выхода на {mag:.1f} В — стабилизатор понизил напряжение"
        return f"Выход выше входа на {mag:.1f} В — стабилизатор поднял напряжение"

    def _voltage_edges(self, reading: dict) -> None:
        volts = reading.get("input_v")
        on_battery = bool(reading.get("on_battery"))
        with self._lock:
            limits = voltage_limits(self.settings)
        shown = "—" if volts is None else f"{float(volts):.1f}"
        checks = (
            ("voltage_crit_low", "bad", f"Входное напряжение опасно низкое: {shown} В", "info", f"Напряжение уже не ниже {limits['crit_low']:.0f} В"),
            ("voltage_low", "warn", f"Входное напряжение понижено: {shown} В", "info", f"Напряжение снова выше {limits['low']:.0f} В"),
            ("voltage_high", "warn", f"Входное напряжение повышено: {shown} В", "info", f"Напряжение снова ниже {limits['high']:.0f} В"),
            ("voltage_crit_high", "bad", f"Входное напряжение опасно высокое: {shown} В", "info", f"Напряжение уже не выше {limits['crit_high']:.0f} В"),
        )
        for code, on_level, on_text, off_level, off_text in checks:
            previous = bool(self._edges.get(code))
            active = mains_voltage_active(code, volts, on_battery, previous, limits)
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
        self._charge_armed = False
        self._charge_full_since = 0.0

    def _note(self, direction: str, text: str) -> None:
        line = {"t": time.time(), "dir": direction, "text": " ".join(str(text).split())[:180]}
        with self._lock:
            self._trace.append(line)
            self._trace = self._trace[-40:]

    def _event(self, level: str, code: str, message: str, force: bool = False) -> None:
        item = {"t": time.time(), "level": level, "code": code, "message": message}
        with self._lock:
            if (
                not force
                and self._events
                and self._events[-1]["code"] == code
                and item["t"] - self._events[-1]["t"] < 20
            ):
                return
            self._events.append(item)
            self._events = self._events[-500:]
            reading = None if self._reading is None else dict(self._reading)
        self._db.execute(
            "INSERT INTO events (ts, level, code, message) VALUES (?, ?, ?, ?)",
            (item["t"], level, code, message),
        )
        self._db.commit()
        self._telegram.notify(item, reading, force=force)
        self._notify_tray(item)
        if item.get("code") in reports.IMPORTANT:
            self._journal_at = 0.0
            self._flush_journal()

    def _notify_tray(self, item: dict) -> None:
        tray = self._tray
        if tray is None or not getattr(tray, "hidden", False):
            return
        code = str(item.get("code") or "")
        if not windows_notify_allowed(self.get_settings_raw(), code):
            return
        title = card_title(code, item.get("message"))
        text = str(item.get("message") or title)
        try:
            png = notify_image(code, title, text)
        except Exception:
            png = None
        try:
            tray.notify(title, text, image=png, code=code, level=str(item.get("level") or ""))
        except Exception:
            pass

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
        kva = reports.normalize_kva(settings.get("ups_kva"))
        if self._journal_day and self._journal_day != stamp:
            try:
                previous = reports.build_day(self._db, self._journal_day, kva)
                root = reports.resolve_journal_dir(settings.get("journal_dir"))
                reports.write_day(root, previous)
                reports.write_summary(root, reports.build_summary(root, previous))
            except OSError:
                pass
        self._journal_day = stamp
        self._journal_at = now
        try:
            day = reports.build_day(self._db, stamp, kva)
            root = reports.resolve_journal_dir(settings.get("journal_dir"))
            reports.write_day(root, day)
            reports.write_summary(root, reports.build_summary(root, day))
        except OSError:
            return

    def _day_report(self, stamp: str | None = None) -> dict:
        day = reports.parse_day(stamp) or reports.today_stamp()
        kva = self._kva()
        with self._lock:
            root = reports.resolve_journal_dir(self.settings.get("journal_dir"))
        self._upgrade_journal_energy(root, kva)
        stored = reports.load_day(root, day)
        built = reports.build_day(self._db, day, kva)
        chosen = built
        if stored and len(stored.get("points") or []) >= len(built.get("points") or []):
            if len(stored.get("events") or []) >= len(built.get("events") or []):
                chosen = reports.upgrade_day_energy(self._db, stored, kva)
                built_stats = built.get("stats") or {}
                stats = dict(chosen.get("stats") or {})
                for key in ("energyKwh", "energyKwhBattery", "energyKwhMains", "avgW", "peakW"):
                    if built_stats.get(key) is not None:
                        stats[key] = built_stats[key]
                chosen["stats"] = stats
        chosen["kva"] = kva
        return chosen

    def _summary_report(self) -> dict:
        kva = self._kva()
        with self._lock:
            root = reports.resolve_journal_dir(self.settings.get("journal_dir"))
            enabled = bool(self.settings.get("journal_enabled", True))
        extra = None
        try:
            extra = reports.build_day(self._db, reports.today_stamp(), kva)
        except sqlite3.Error:
            extra = None
        if enabled:
            try:
                reports.ensure_journal(root)
                if extra:
                    reports.write_day(root, extra)
            except OSError:
                pass
        self._upgrade_journal_energy(root, kva)
        summary = reports.build_summary(root, extra)
        summary["kva"] = kva
        days = [reports.ensure_energy(item, kva) for item in summary.get("days") or []]
        summary["days"] = days
        summary["stats"] = reports.summary_stats(days)
        return summary

    def _upgrade_journal_energy(self, root, kva: float) -> None:
        changed = False
        for stamp in reports.list_days(root):
            stored = reports.load_day(root, stamp)
            if not stored or not reports.missing_energy(stored.get("stats")):
                continue
            try:
                upgraded = reports.upgrade_day_energy(self._db, stored, kva)
            except sqlite3.Error:
                continue
            if reports.missing_energy(upgraded.get("stats")):
                continue
            try:
                reports.write_day(root, upgraded)
                changed = True
            except OSError:
                continue
        if changed:
            try:
                reports.write_summary(root, reports.build_summary(root))
            except OSError:
                return

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
            CREATE INDEX IF NOT EXISTS readings_live_ts ON readings(source, ts);
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
    keep_tail = min(180, max(24, limit // 4))
    tail = points[-keep_tail:]
    head = points[:-keep_tail]
    budget = max(1, limit - len(tail))
    if len(head) <= budget:
        return head + tail
    t0 = float(head[0]["t"])
    t1 = float(head[-1]["t"])
    span = max(1e-3, t1 - t0)
    buckets: dict[int, dict] = {}
    for point in head:
        idx = min(budget - 1, int((float(point["t"]) - t0) / span * (budget - 1)))
        buckets[idx] = point
    cut = tail[0]["t"]
    merged = [buckets[idx] for idx in sorted(buckets) if buckets[idx]["t"] < cut]
    return merged + tail


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


_TG_EVENT_CODES = {
    "on_battery", "on_battery_clear", "voltage_low", "voltage_low_clear",
    "voltage_high", "voltage_high_clear", "voltage_crit_low", "voltage_crit_low_clear",
    "voltage_crit_high", "voltage_crit_high_clear", "battery_boost", "battery_boost_clear",
    "battery_buck", "battery_buck_clear",
    "battery_low", "battery_low_clear",
    "battery_loose", "battery_loose_clear", "battery_swap", "fault", "fault_clear",
    "link_lost", "link", "pc_shutdown", "pc_off", "pc_reboot", "pc_sleep", "pc_hibernate",
    "startup", "shutdown", "app_restart",
}


def _clean_telegram_events(value) -> dict:
    if not isinstance(value, dict):
        return {}
    clean = {}
    for code, item in value.items():
        if code not in _TG_EVENT_CODES:
            continue
        if isinstance(item, dict):
            clean[code] = {
                "on": bool(item.get("on", True)),
                "sound": bool(item.get("sound", True)),
                "tray": bool(item.get("tray", default_event_tray(code))),
            }
        else:
            clean[code] = {
                "on": bool(item),
                "sound": default_event_sound(code),
                "tray": default_event_tray(code),
            }
    return clean


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
            "telegram_digest", "autostart", "journal_enabled", "windows_notify",
            "q1_battery_low",
        }:
            clean[key] = bool(value)
            continue
        if key == "telegram_events":
            clean[key] = _clean_telegram_events(value)
            continue
        if key == "spark_lines":
            allowed = {"in", "out", "load", "batt", "soc", "hz", "temp"}
            lines = [str(item) for item in (value or []) if str(item) in allowed]
            clean[key] = lines or ["in", "out"]
            continue
        if key == "chart_minutes":
            try:
                minutes = int(value)
            except (TypeError, ValueError):
                return {}, "Масштаб графика должен быть числом"
            if minutes not in {15, 60, 360, 1440}:
                return {}, "Масштаб графика: 15, 60, 360 или 1440 минут"
            clean[key] = minutes
            continue
        if key == "chart_series":
            series = str(value or "")
            if series == "battery":
                series = "charge"
            if series not in {"voltage", "load", "charge", "freq", "temp"}:
                return {}, "Неизвестный тип графика"
            clean[key] = series
            continue
        if key == "journal_dir":
            text = str(value or "").strip()
            if "\x00" in text or len(text) > 240:
                return {}, "Путь к журналу слишком длинный"
            clean[key] = str(reports.resolve_journal_dir(text))
            continue
        if key == "ups_kva":
            try:
                kva = float(str(value).strip().replace(",", "."))
            except (TypeError, ValueError):
                return {}, "Мощность ИБП должна быть числом"
            if 20 < kva <= 20000:
                kva /= 1000
            kva = round(kva, 3)
            if not 0.2 <= kva <= 20:
                return {}, "Мощность ИБП: от 0,2 до 20 кВА, либо ВА целиком (например 720)"
            clean[key] = kva
            continue
        if key == "telegram_digest_time":
            clock = parse_clock(value)
            if not clock:
                return {}, "Время отчёта должно быть в формате ЧЧ:ММ"
            clean[key] = clock
            continue
        if key in {"window_x", "window_y"}:
            if value is None or value == "":
                clean[key] = None
                continue
            try:
                coord = int(value)
            except (TypeError, ValueError):
                return {}, "Положение окна должно быть числом"
            if not -20000 <= coord <= 20000:
                return {}, "Положение окна вне экрана"
            clean[key] = coord
            continue
        if key in {"window_width", "window_height"}:
            try:
                size = int(value)
            except (TypeError, ValueError):
                return {}, "Размер окна должен быть числом"
            low, high = (960, 8000) if key == "window_width" else (640, 5000)
            if not low <= size <= high:
                return {}, "Размер окна вне допустимых пределов"
            clean[key] = size
            continue
        if key == "window_maximized":
            clean[key] = bool(value)
            continue
        if key not in _LIMITS:
            continue
        low, high = _LIMITS[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            return {}, f"Поле «{key}» должно быть числом"
        if key in {"poll_seconds", "battery_count", "battery_groups", "battery_health", "shutdown_delay", "spark_window"}:
            number = int(number)
        if not low <= number <= high:
            return {}, f"Поле «{key}» вне допустимых пределов"
        clean[key] = number
    full = clean.get("full_voltage", DEFAULTS["full_voltage"])
    eod = clean.get("eod_voltage", DEFAULTS["eod_voltage"])
    low_cell = clean.get("low_voltage", DEFAULTS["low_voltage"])
    if eod >= full:
        return {}, "Конец разряда должен быть ниже напряжения полного заряда"
    if not eod < low_cell < full:
        return {}, "Порог «садится» должен быть между концом разряда и полным зарядом"
    crit_low = clean.get("volt_crit_low", DEFAULTS["volt_crit_low"])
    low = clean.get("volt_low", DEFAULTS["volt_low"])
    high = clean.get("volt_high", DEFAULTS["volt_high"])
    crit_high = clean.get("volt_crit_high", DEFAULTS["volt_crit_high"])
    if not crit_low < low < high < crit_high:
        return {}, "Пороги сети: сверхнизкое < низкое < высокое < сверхвысокое"
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
        public_chat(item)
        for item in data.get("telegram_chats") or []
        if isinstance(item, dict)
    ]
    data["autostart"] = autostart.is_enabled()
    return data
