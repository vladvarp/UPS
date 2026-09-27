"""Telegram bot: power alerts, reports, and PC power buttons."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from pathlib import Path

from ups.notify_art import card_title, notify_image

TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{20,}$")
CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")
_OFFSET_PATH = Path(__file__).resolve().parent.parent / "data" / "telegram_offset.txt"
_CALLBACK_MAX_AGE = 120

_MENUS = {
    "root": [["ПК", "ПО", "Отчёты"]],
    "pc": [
        ["Выключить ПК", "Сон"],
        ["Перезагрузка", "Гибернация"],
        ["Отмена выключения", "Назад"],
    ],
    "app": [
        ["Скриншот", "Перезапуск программы"],
        ["Назад"],
    ],
    "reports": [
        ["Состояние", "Журнал"],
        ["Отчёт дня", "Сводка"],
        ["Назад"],
    ],
}

_POWER = {
    "выключить пк": "pc_off",
    "перезагрузка": "pc_reboot",
    "сон": "pc_sleep",
    "гибернация": "pc_hibernate",
}

_CONFIRM = {
    "pc_off": "Выключить этот компьютер через 20 секунд?",
    "pc_reboot": "Перезагрузить этот компьютер через 20 секунд?",
    "pc_sleep": "Усыпить этот компьютер сейчас?",
    "pc_hibernate": "Увести этот компьютер в гибернацию?",
    "app_restart": "Перезапустить программу UPS?",
}

def event_pref(settings: dict | None, code: str) -> tuple[bool, bool]:
    src = settings or {}
    item = (src.get("telegram_events") or {}).get(code)
    if isinstance(item, dict):
        return bool(item.get("on", True)), bool(item.get("sound", True))
    if isinstance(item, bool):
        return item, not str(code).endswith("_clear")
    if code in {"on_battery", "on_battery_clear"}:
        return bool(src.get("telegram_notify_power", True)), True
    if code in {"startup", "shutdown"}:
        return True, code == "shutdown"
    return bool(src.get("telegram_notify_alerts", True)), not str(code).endswith("_clear")


_ALERTS = {
    "on_battery": True,
    "on_battery_clear": True,
    "battery_low": True,
    "battery_low_clear": True,
    "fault": True,
    "fault_clear": True,
    "link_lost": True,
    "pc_shutdown": True,
    "pc_off": True,
    "pc_reboot": True,
    "pc_sleep": True,
    "pc_hibernate": True,
    "battery_loose": True,
    "battery_loose_clear": True,
    "battery_swap": True,
    "app_restart": True,
    "voltage_low": True,
    "voltage_low_clear": True,
    "voltage_high": True,
    "voltage_high_clear": True,
    "voltage_crit_low": True,
    "voltage_crit_low_clear": True,
    "voltage_crit_high": True,
    "voltage_crit_high_clear": True,
}


class TelegramBridge:
    def __init__(self, host) -> None:
        self._host = host
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._token = ""
        self._offset = 0
        self._ok = False
        self._username = ""
        self._error = ""
        self._digest_day = ""
        self._startup_sent = False
        self._boards: dict[int, str] = {}
        self._offset = _load_offset()
        self._backlog_cleared = False

    def announce_stop(self, timeout: float = 5.0) -> None:
        settings = self._host.get_settings_raw()
        token = str(self._token or settings.get("telegram_token") or "").strip()
        chats = [
            item for item in (settings.get("telegram_chats") or [])
            if isinstance(item, dict) and item.get("id") is not None
        ]
        if not token or not chats:
            return
        send, sound = event_pref(settings, "shutdown")
        if not send:
            return
        stamp = datetime.now().strftime("%H:%M")
        text = f"UPS закрыт в {stamp}"
        done = threading.Event()

        def work() -> None:
            try:
                png = notify_image("shutdown", card_title("shutdown", text), text)
                limit = max(1.0, float(timeout) / max(1, len(chats)))
                for item in chats:
                    chat_id = item.get("id")
                    try:
                        _send_photo(token, chat_id, png, text, menu=self._board(chat_id), timeout=limit, silent=not sound)
                    except TelegramError:
                        try:
                            _send(token, chat_id, text, menu=self._board(chat_id), timeout=limit, silent=not sound)
                        except TelegramError:
                            pass
            except Exception:
                pass
            finally:
                done.set()

        threading.Thread(target=work, name="ups-telegram-stop", daemon=True).start()
        done.wait(max(0.2, float(timeout)))

    def start(self) -> None:
        self.configure(self._host.get_settings_raw())
        self._thread = threading.Thread(target=self._loop, name="ups-telegram", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def configure(self, settings: dict) -> None:
        token = str(settings.get("telegram_token") or "").strip()
        with self._lock:
            if token != self._token:
                previous = self._token
                self._token = token
                if previous:
                    self._offset = 0
                    _save_offset(0)
                self._ok = False
                self._username = ""
                self._error = "" if token else "токен не задан"
                self._startup_sent = False
                self._backlog_cleared = False

    def status(self) -> dict:
        settings = self._host.get_settings_raw()
        chats = settings.get("telegram_chats") or []
        with self._lock:
            return {
                "configured": bool(self._token),
                "ok": self._ok,
                "username": self._username,
                "error": self._error,
                "chats": [
                    {"id": item.get("id"), "name": item.get("name") or str(item.get("id"))}
                    for item in chats
                    if isinstance(item, dict)
                ],
                "pair_code": str(settings.get("telegram_pair") or ""),
            }

    def notify(self, event: dict, reading: dict | None, force: bool = False) -> None:
        settings = self._host.get_settings_raw()
        if not settings.get("telegram_token"):
            return
        code = event.get("code") or ""
        send, _sound = event_pref(settings, code)
        if not force:
            if not send:
                return
            if code not in _ALERTS:
                return
        text = format_alert(event, reading)
        self._broadcast_notice(str(code), text, force=force)

    def _loop(self) -> None:
        while not self._stop.is_set():
            token = self._token
            if not token:
                self._stop.wait(1.0)
                continue
            if not self._ok:
                if not self._hello(token):
                    self._stop.wait(8)
                    continue
                if not self._backlog_cleared:
                    self._backlog_cleared = self._skip_pending(token)
            self._announce_start()
            self._maybe_digest()
            try:
                data = _api(token, "getUpdates", {
                    "timeout": 8,
                    "offset": self._offset,
                    "allowed_updates": ["message", "callback_query"],
                }, timeout=12)
            except TelegramError as exc:
                with self._lock:
                    self._ok = False
                    self._error = str(exc)
                self._stop.wait(6)
                continue
            for update in data.get("result") or []:
                self._offset = int(update.get("update_id", 0)) + 1
                _save_offset(self._offset)
                try:
                    self._handle(update)
                except Exception as exc:
                    self._error = str(exc)

    def _hello(self, token: str) -> bool:
        try:
            data = _api(token, "getMe", timeout=8)
            me = data.get("result") or {}
            username = str(me.get("username") or "")
            with self._lock:
                self._ok = True
                self._username = username
                self._error = ""
            self._install_menu(token)
            return True
        except TelegramError as exc:
            with self._lock:
                self._ok = False
                self._error = str(exc)
            return False

    def _install_menu(self, token: str) -> None:
        try:
            _api(token, "setMyCommands", {
                "commands": [
                    {"command": "status", "description": "Запрос состояния"},
                    {"command": "screenshot", "description": "Скриншот"},
                    {"command": "events", "description": "Журнал"},
                ],
                "scope": {"type": "default"},
            }, timeout=8)
            _api(token, "setChatMenuButton", {
                "menu_button": {"type": "commands"},
            }, timeout=8)
        except TelegramError:
            pass

    def _handle(self, update: dict) -> None:
        if "callback_query" in update:
            self._callback(update["callback_query"])
            return
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or not text:
            return
        name = chat.get("username") or chat.get("first_name") or str(chat_id)
        if text.startswith("/start"):
            self._start_chat(int(chat_id), name, text)
            return
        if not self._known(int(chat_id)):
            _send(self._token, chat_id, "Этот чат ещё не привязан. Код из программы:\n/start <код>")
            return
        lowered = _plain(text)
        if lowered in {"пк", "компьютер"}:
            self._open_menu(chat_id, "pc", "Питание компьютера.")
            return
        if lowered in {"по", "программа"}:
            self._open_menu(chat_id, "app", "Программа UPS.")
            return
        if lowered in {"отчёты", "отчеты"}:
            self._open_menu(chat_id, "reports", "Отчёты и журнал.")
            return
        if lowered in {"назад", "меню"}:
            self._open_menu(chat_id, "root", "Главное меню.")
            return
        head = lowered.split()[0] if lowered else ""
        if lowered in {"состояние", "status", "запрос состояния"} or head in {"состояние", "status"}:
            self._notice(chat_id, "status", self._host.telegram_status_text())
            return
        if lowered in {"отчёт дня", "отчет дня", "отчёт", "отчет", "report"}:
            self._send_html(chat_id, "day_report", *self._host.telegram_day_html())
            return
        if lowered in {"сводка", "сводка дней", "summary"}:
            self._send_html(chat_id, "summary", *self._host.telegram_summary_html())
            return
        if lowered in {"неделя", "week"}:
            self._notice(chat_id, "report", self._host.telegram_report_text(24 * 7))
            return
        if lowered in {"журнал", "events"} or head in {"журнал", "events"}:
            self._notice(chat_id, "events", self._host.telegram_events_text())
            return
        if lowered in {"скриншот", "screenshot", "экран"} or head in {"скриншот", "screenshot", "экран"}:
            self._send_screenshot(chat_id)
            return
        if lowered in {"перезапуск программы", "перезагрузить программу", "рестарт"}:
            self._ask_power(chat_id, "app_restart")
            return
        if lowered in {"отмена выключения", "cancel", "отмена"}:
            result = self._host.command("cancel_shutdown")
            self._notice(chat_id, "cancel", result.get("message") or "Готово")
            return
        if lowered in {"помощь", "help", "start"}:
            self._notice(chat_id, "help", help_text(self._username))
            return
        if lowered in _POWER:
            self._ask_power(chat_id, _POWER[lowered])
            return
        _send(self._token, chat_id, "Не понял. Нажмите кнопку внизу или /help.", menu=self._board(chat_id))

    def _start_chat(self, chat_id: int, name: str, text: str) -> None:
        settings = self._host.get_settings_raw()
        pair = str(settings.get("telegram_pair") or "")
        parts = text.split()
        code = parts[1] if len(parts) > 1 else ""
        if self._known(chat_id):
            self._open_menu(chat_id, "root", f"Чат уже привязан, {name}.")
            return
        if not pair or code != pair:
            _send(self._token, chat_id, "Чтобы привязать чат, откройте вкладку Телеграм в UPS и напишите:\n/start <код>")
            return
        self._host.link_telegram_chat({"id": chat_id, "name": name})
        self._open_menu(chat_id, "root", f"Готово. Этот чат получает сообщения UPS.\n\n{help_text(self._username)}")

    def _send_html(self, chat_id: int, code: str, body: bytes | None, name: str) -> None:
        if not body:
            self._notice(chat_id, code, name or "Отчёт ещё пуст.")
            return
        try:
            _send_document(
                self._token,
                chat_id,
                body,
                name,
                "Откройте файл в браузере телефона — фильтры уже внутри.",
                menu=self._board(chat_id),
            )
        except TelegramError as exc:
            self._notice(chat_id, code, f"HTML не ушёл: {exc}")

    def _send_screenshot(self, chat_id: int) -> None:
        png = self._host.telegram_screenshot()
        if not png:
            self._notice(chat_id, "screenshot", "Не удалось снять главное окно. UPS должен быть запущен.")
            return
        try:
            _send_photo(self._token, chat_id, png, "Главное окно UPS", menu=self._board(chat_id))
        except TelegramError as exc:
            _send(self._token, chat_id, f"Скриншот не ушёл: {exc}", menu=self._board(chat_id))

    def _ask_power(self, chat_id: int, action: str) -> None:
        self._notice(
            chat_id,
            action,
            _CONFIRM[action],
            inline=[[
                {"text": "Да", "callback_data": f"{action}:yes"},
                {"text": "Нет", "callback_data": f"{action}:no"},
            ]],
        )

    def _callback(self, query: dict) -> None:
        data = str(query.get("data") or "")
        chat = (query.get("message") or {}).get("chat") or {}
        chat_id = chat.get("id")
        qid = query.get("id")
        if qid:
            try:
                _api(self._token, "answerCallbackQuery", {"callback_query_id": qid}, timeout=8)
            except TelegramError:
                pass
        if chat_id is None or not self._known(int(chat_id)):
            return
        if not callback_fresh(query):
            self._notice(chat_id, "cancel", "Кнопка устарела. Нажмите действие ещё раз.")
            return
        action, _, answer = data.partition(":")
        if answer != "yes" or action not in _CONFIRM:
            self._notice(chat_id, "cancel", "Отменено.")
            return
        if action == "app_restart":
            self._ack_offset()
        result = self._host.command(action)
        if action != "app_restart":
            self._notice(chat_id, action, result.get("message") or "Готово")

    def _known(self, chat_id: int) -> bool:
        settings = self._host.get_settings_raw()
        for item in settings.get("telegram_chats") or []:
            if isinstance(item, dict) and int(item.get("id") or 0) == chat_id:
                return True
        return False

    def _board(self, chat_id) -> str:
        return self._boards.get(int(chat_id), "root")

    def _open_menu(self, chat_id, name: str, text: str) -> None:
        self._boards[int(chat_id)] = name
        _send(self._token, chat_id, text, menu=name)

    def _skip_pending(self, token: str) -> bool:
        try:
            data = _api(token, "getUpdates", {
                "timeout": 0,
                "offset": self._offset,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=8)
        except TelegramError:
            return False
        results = data.get("result") or []
        if results:
            self._offset = int(results[-1].get("update_id", 0)) + 1
            _save_offset(self._offset)
        return True

    def _ack_offset(self) -> None:
        token = self._token
        if not token:
            return
        try:
            _api(token, "getUpdates", {
                "timeout": 0,
                "offset": self._offset,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=5)
        except TelegramError:
            pass

    def _announce_start(self) -> None:
        if self._startup_sent:
            return
        settings = self._host.get_settings_raw()
        chats = settings.get("telegram_chats") or []
        if not chats:
            return
        self._startup_sent = True
        stamp = datetime.now().strftime("%H:%M")
        self._broadcast_notice("startup", f"UPS запущен в {stamp}\n\n{self._host.telegram_status_text()}")

    def _notice(self, chat_id, code: str, text: str, inline: list | None = None, silent: bool = False) -> None:
        token = self._token
        if not token or not text:
            return
        png = notify_image(code, card_title(code, text), text)
        try:
            _send_photo(token, chat_id, png, text[:1024], inline=inline, menu=self._board(chat_id), silent=silent)
            if len(text) > 1024:
                _send(token, chat_id, text[1024:], menu=None if inline else self._board(chat_id), silent=silent)
        except TelegramError:
            _send(token, chat_id, text, menu=None if inline else self._board(chat_id), inline=inline, silent=silent)

    def _broadcast_notice(self, code: str, text: str, force: bool = False) -> None:
        token = self._token
        if not token or not text:
            return
        settings = self._host.get_settings_raw()
        send, sound = event_pref(settings, code)
        if not send and not force:
            return
        silent = not sound
        for item in settings.get("telegram_chats") or []:
            if not isinstance(item, dict):
                continue
            chat_id = item.get("id")
            if chat_id is None:
                continue
            try:
                self._notice(chat_id, code, text, silent=silent)
            except TelegramError as exc:
                with self._lock:
                    self._error = str(exc)

    def _broadcast(self, text: str) -> None:
        token = self._token
        if not token or not text:
            return
        settings = self._host.get_settings_raw()
        for item in settings.get("telegram_chats") or []:
            if not isinstance(item, dict):
                continue
            chat_id = item.get("id")
            if chat_id is None:
                continue
            try:
                _send(token, chat_id, text, menu="root")
            except TelegramError as exc:
                with self._lock:
                    self._error = str(exc)

    def _maybe_digest(self) -> None:
        settings = self._host.get_settings_raw()
        if not settings.get("telegram_digest"):
            return
        now = datetime.now()
        if not digest_due(now, str(settings.get("telegram_digest_time") or "21:00")):
            return
        stamp = now.strftime("%Y-%m-%d")
        if self._digest_day == stamp:
            return
        self._digest_day = stamp
        self._broadcast_notice("report", self._host.telegram_report_text(24))


class TelegramError(RuntimeError):
    pass


def callback_fresh(query: dict, now: float | None = None, max_age: int = _CALLBACK_MAX_AGE) -> bool:
    stamp = ((query.get("message") or {}).get("date"))
    try:
        sent = float(stamp)
    except (TypeError, ValueError):
        return True
    return (now if now is not None else time.time()) - sent <= max_age


def _load_offset() -> int:
    try:
        return max(0, int(_OFFSET_PATH.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return 0


def _save_offset(value: int) -> None:
    try:
        _OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OFFSET_PATH.write_text(str(int(value)), encoding="utf-8")
    except OSError:
        pass


def parse_clock(value) -> str | None:
    match = CLOCK_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def digest_due(now: datetime, clock: str, window: int = 8) -> bool:
    parsed = parse_clock(clock) or "21:00"
    hour, minute = (int(part) for part in parsed.split(":"))
    target = hour * 60 + minute
    current = now.hour * 60 + now.minute
    return 0 <= current - target < window


def _plain(text: str) -> str:
    cleaned = re.sub(r"[^\wа-яё]+", " ", text.lower().lstrip("/"), flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def format_alert(event: dict, reading: dict | None) -> str:
    title = event.get("message") or "Событие"
    lines = [str(title)]
    if reading:
        extra = status_lines(reading)
        if extra:
            lines.append("")
            lines.extend(extra)
    return "\n".join(lines)


def status_lines(reading: dict) -> list[str]:
    if not reading:
        return ["ИБП пока молчит."]
    load = reading.get("load_pct")
    watts = reading.get("load_w")
    runtime = reading.get("runtime_min")
    load_line = "Нагрузка —"
    if load is not None:
        load_line = f"Нагрузка {fmt(load, 0)}%"
        if watts is not None:
            load_line += f" · ≈ {watts} Вт"
    lines = [
        f"Сеть: {'есть' if reading.get('mains_ok') else 'нет'}",
        f"Вход {fmt(reading.get('input_v'))} В · выход {fmt(reading.get('output_v'))} В",
    ]
    volt_note = reading.get("voltage_note")
    if volt_note:
        lines.append(str(volt_note))
    lines.extend([
        load_line,
        f"Батарея {fmt(reading.get('battery_v'))} В · {fmt(reading.get('battery_pct'), 0)}%",
    ])
    if reading.get("battery_loose"):
        lines.append("Батареи: напряжение скачет, похоже отключены")
    swap = reading.get("battery_swap")
    if isinstance(swap, dict) and swap.get("from") is not None and swap.get("to") is not None:
        lines.append(f"Пакет сменился: {swap['from']} → {swap['to']} В")
    if runtime is not None:
        lines.append(f"Автономия ≈ {runtime} мин")
    if reading.get("on_battery") and reading.get("discharge_seconds"):
        lines.append(f"Разряд {clock(reading['discharge_seconds'])}")
    mode = reading.get("mode_label")
    if mode:
        lines.append(mode)
    return lines


def format_report(hours: int, points: list[dict], events: list[dict], reading: dict | None) -> str:
    title = f"Отчёт за {hours} ч" if hours != 24 else "Отчёт за сутки"
    if not points:
        body = ["За этот срок записей нет."]
    else:
        ins = [p["in"] for p in points if p.get("in") is not None and p["in"] >= 80]
        loads = [p["load"] for p in points if p.get("load") is not None]
        outages = _outage_stats(events)
        body = [
            f"Сеть пропадала: {_times(outages['count'])}, всего {clock(outages['seconds'])}",
        ]
        if ins:
            body.append(f"Вход: {min(ins):.0f}–{max(ins):.0f} В, среднее {sum(ins)/len(ins):.1f}")
        if loads:
            avg = sum(loads) / len(loads)
            peak = max(loads)
            watts = reading.get("load_w") if reading else None
            kva = None
            if watts is not None and reading and reading.get("load_pct"):
                try:
                    kva = watts / (10 * float(reading["load_pct"]))
                except (TypeError, ValueError, ZeroDivisionError):
                    kva = None
            line = f"Нагрузка: средняя {avg:.0f}%, пик {peak:.0f}%"
            if kva:
                line += f" · ≈ {int(round(kva * 10 * avg))}–{int(round(kva * 10 * peak))} Вт"
            body.append(line)
    lines = [title, *body]
    if reading:
        lines += ["", "Сейчас:", *status_lines(reading)]
    recent = [item for item in events if item.get("code") in _ALERTS][-6:]
    if recent:
        lines += ["", "События:"]
        for item in recent:
            stamp = datetime.fromtimestamp(item["t"]).strftime("%d.%m %H:%M")
            lines.append(f"{stamp} · {item.get('message') or ''}")
    return "\n".join(lines)


def format_events(events: list[dict], limit: int = 8) -> str:
    if not events:
        return "Журнал пуст."
    lines = ["Последние события:"]
    for item in events[-limit:]:
        stamp = datetime.fromtimestamp(item["t"]).strftime("%d.%m %H:%M")
        lines.append(f"{stamp} · {item.get('message') or ''}")
    return "\n".join(lines)


def help_text(username: str) -> str:
    who = f"@{username}" if username else "бот"
    return (
        f"{who} следит за питанием.\n"
        "ПК — выключение, сон, перезагрузка и гибернация.\n"
        "ПО — скриншот и перезапуск программы.\n"
        "Отчёты — состояние, журнал, отчёт дня и сводка в HTML.\n"
        "Слева от поля ввода — состояние, скриншот и журнал."
    )


def fmt(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if digits == 0:
        return str(int(round(number)))
    return f"{number:.{digits}f}"


def clock(total) -> str:
    seconds = max(0, int(total or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    if minutes:
        return f"{minutes} мин {secs:02d} с"
    return f"{secs} с"


def _times(count: int) -> str:
    number = max(0, int(count))
    mod10 = number % 10
    mod100 = number % 100
    if mod10 == 1 and mod100 != 11:
        word = "раз"
    elif mod10 in {2, 3, 4} and mod100 not in {12, 13, 14}:
        word = "раза"
    else:
        word = "раз"
    return f"{number} {word}"


def _outage_stats(events: list[dict]) -> dict:
    count = 0
    seconds = 0.0
    started = None
    for item in sorted(events, key=lambda row: row.get("t") or 0):
        code = item.get("code")
        stamp = float(item.get("t") or 0)
        if code == "on_battery":
            started = stamp
            count += 1
        elif code == "on_battery_clear" and started is not None:
            seconds += max(0.0, stamp - started)
            started = None
    if started is not None:
        seconds += max(0.0, time.time() - started)
    return {"count": count, "seconds": int(seconds)}


def _api(token: str, method: str, payload: dict | None = None, timeout: float = 12) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail).get("description") or detail
        except json.JSONDecodeError:
            message = detail or str(exc)
        raise TelegramError(str(message)[:160]) from exc
    except urllib.error.URLError as exc:
        raise TelegramError("нет связи с Telegram") from exc
    except TimeoutError as exc:
        raise TelegramError("Telegram не ответил") from exc
    if not data.get("ok"):
        raise TelegramError(str(data.get("description") or "ответ Telegram без ok")[:160])
    return data


def _menu_rows(name: str | None) -> list[list[str]]:
    return _MENUS.get(name or "root") or _MENUS["root"]


def _send(token: str, chat_id, text: str, menu: str | None = None, inline: list | None = None, timeout: float = 12, silent: bool = False) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
        "disable_notification": bool(silent),
    }
    if inline:
        payload["reply_markup"] = {"inline_keyboard": [inline] if inline and isinstance(inline[0], dict) else inline}
    elif menu:
        payload["reply_markup"] = {
            "keyboard": [[{"text": cell} for cell in row] for row in _menu_rows(menu)],
            "resize_keyboard": True,
        }
    _api(token, "sendMessage", payload, timeout=timeout)


def _send_photo(token: str, chat_id, png: bytes, caption: str = "", inline: list | None = None, menu: str | None = "root", timeout: float = 20, silent: bool = False) -> None:
    boundary = "----UPSFormBoundary7"
    if inline:
        markup = json.dumps({
            "inline_keyboard": [inline] if inline and isinstance(inline[0], dict) else inline,
        }, ensure_ascii=False)
    else:
        markup = json.dumps({
            "keyboard": [[{"text": cell} for cell in row] for row in _menu_rows(menu)],
            "resize_keyboard": True,
        }, ensure_ascii=False)
    parts = []

    def field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )

    field("chat_id", str(chat_id))
    if caption:
        field("caption", caption[:1024])
    if silent:
        field("disable_notification", "true")
    field("reply_markup", markup)
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"ups.png\"\r\n"
        f"Content-Type: image/png\r\n\r\n".encode("ascii") + png + b"\r\n"
        + f"--{boundary}--\r\n".encode("ascii")
    )
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail).get("description") or detail
        except json.JSONDecodeError:
            message = detail or str(exc)
        raise TelegramError(str(message)[:160]) from exc
    except urllib.error.URLError as exc:
        raise TelegramError("нет связи с Telegram") from exc
    except TimeoutError as exc:
        raise TelegramError("Telegram не ответил") from exc
    if not data.get("ok"):
        raise TelegramError(str(data.get("description") or "скриншот не принят")[:160])


def _send_document(token: str, chat_id, body: bytes, filename: str, caption: str = "", menu: str | None = "root") -> None:
    boundary = "----UPSFormBoundary8"
    markup = json.dumps({
        "keyboard": [[{"text": cell} for cell in row] for row in _menu_rows(menu)],
        "resize_keyboard": True,
    }, ensure_ascii=False)
    safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in filename) or "UPS.html"
    parts = []

    def field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )

    field("chat_id", str(chat_id))
    if caption:
        field("caption", caption[:1024])
    field("reply_markup", markup)
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{safe_name}\"\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n\r\n".encode("ascii") + body + b"\r\n"
        + f"--{boundary}--\r\n".encode("ascii")
    )
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail).get("description") or detail
        except json.JSONDecodeError:
            message = detail or str(exc)
        raise TelegramError(str(message)[:160]) from exc
    except urllib.error.URLError as exc:
        raise TelegramError("нет связи с Telegram") from exc
    if not data.get("ok"):
        raise TelegramError(str(data.get("description") or "файл не принят")[:160])
