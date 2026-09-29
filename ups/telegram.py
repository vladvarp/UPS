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
    "root_admin": [["ПК", "ПО", "Отчёты"], ["Чаты"]],
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

def default_event_sound(code: str) -> bool:
    return not str(code).endswith("_clear") and code != "startup"


def default_event_tray(code: str) -> bool:
    return not str(code).endswith("_clear") and code not in {"startup", "shutdown"}


def event_pref(settings: dict | None, code: str) -> tuple[bool, bool]:
    src = settings or {}
    item = (src.get("telegram_events") or {}).get(code)
    if isinstance(item, dict):
        return bool(item.get("on", True)), bool(item.get("sound", True))
    if isinstance(item, bool):
        return item, default_event_sound(code)
    if code in {"on_battery", "on_battery_clear"}:
        return bool(src.get("telegram_notify_power", True)), True
    if code in {"startup", "shutdown"}:
        return True, code == "shutdown"
    return bool(src.get("telegram_notify_alerts", True)), default_event_sound(code)


def tray_pref(settings: dict | None, code: str) -> bool:
    src = settings or {}
    item = (src.get("telegram_events") or {}).get(code)
    if isinstance(item, dict) and "tray" in item:
        return bool(item.get("tray"))
    return default_event_tray(code)


def windows_notify_allowed(settings: dict | None, code: str) -> bool:
    src = settings or {}
    if not src.get("windows_notify", True):
        return False
    return tray_pref(src, code)


BOT_ACTIONS = (
    {"code": "pc_off", "group": "ПК", "title": "Выключить ПК"},
    {"code": "pc_reboot", "group": "ПК", "title": "Перезагрузка"},
    {"code": "pc_sleep", "group": "ПК", "title": "Сон"},
    {"code": "pc_hibernate", "group": "ПК", "title": "Гибернация"},
    {"code": "cancel_shutdown", "group": "ПК", "title": "Отмена выключения"},
    {"code": "screenshot", "group": "ПО", "title": "Скриншот"},
    {"code": "app_restart", "group": "ПО", "title": "Перезапуск программы"},
    {"code": "status", "group": "Отчёты", "title": "Состояние"},
    {"code": "events", "group": "Отчёты", "title": "Журнал"},
    {"code": "day_report", "group": "Отчёты", "title": "Отчёт дня"},
    {"code": "summary", "group": "Отчёты", "title": "Сводка"},
)

_ACTION_CODES = {item["code"] for item in BOT_ACTIONS}
_GROUP_ACTIONS = {
    "pc": {"pc_off", "pc_reboot", "pc_sleep", "pc_hibernate", "cancel_shutdown"},
    "app": {"screenshot", "app_restart"},
    "reports": {"status", "events", "day_report", "summary"},
}
_BUTTON_ACTION = {
    "ПК": "menu_pc",
    "ПО": "menu_app",
    "Отчёты": "menu_reports",
    "Выключить ПК": "pc_off",
    "Сон": "pc_sleep",
    "Перезагрузка": "pc_reboot",
    "Гибернация": "pc_hibernate",
    "Отмена выключения": "cancel_shutdown",
    "Скриншот": "screenshot",
    "Перезапуск программы": "app_restart",
    "Состояние": "status",
    "Журнал": "events",
    "Отчёт дня": "day_report",
    "Сводка": "summary",
}


def default_guest_actions() -> dict:
    return {
        "pc_off": False,
        "pc_reboot": False,
        "pc_sleep": False,
        "pc_hibernate": False,
        "cancel_shutdown": True,
        "screenshot": True,
        "app_restart": False,
        "status": True,
        "events": True,
        "day_report": True,
        "summary": True,
    }


def default_guest_events(settings: dict | None = None) -> dict:
    return {
        code: {
            "on": code in {"on_battery", "on_battery_clear"},
            "sound": code == "on_battery",
        }
        for code in EVENT_CODES
    }


def clean_guest_actions(value) -> dict:
    defaults = default_guest_actions()
    if not isinstance(value, dict):
        return dict(defaults)
    return {code: bool(value.get(code, defaults[code])) for code in defaults}


def _guest_event_item(code: str, value) -> dict:
    if isinstance(value, dict):
        on = bool(value.get("on", False))
        sound = bool(value.get("sound", on and default_event_sound(code)))
        return {"on": on, "sound": sound and on}
    on = bool(value)
    return {"on": on, "sound": on and default_event_sound(code)}


def clean_guest_events(value) -> dict:
    src = value if isinstance(value, dict) else {}
    return {code: _guest_event_item(code, src.get(code, False)) for code in EVENT_CODES}


def public_chat(item: dict) -> dict:
    guest = bool(item.get("guest"))
    admin = bool(item.get("admin")) and not guest
    chat = {
        "id": item.get("id"),
        "name": item.get("name") or "",
        "guest": guest,
        "admin": admin,
    }
    if guest:
        stored_events = item.get("events") if isinstance(item.get("events"), dict) else None
        stored_actions = item.get("actions") if isinstance(item.get("actions"), dict) else None
        chat["events"] = clean_guest_events(stored_events if stored_events is not None else default_guest_events())
        chat["actions"] = clean_guest_actions(stored_actions)
    return chat


def find_chat(settings: dict | None, chat_id) -> dict | None:
    try:
        want = int(chat_id)
    except (TypeError, ValueError):
        return None
    for item in (settings or {}).get("telegram_chats") or []:
        if isinstance(item, dict) and int(item.get("id") or 0) == want:
            return item
    return None


def chat_has_action_rules(chat: dict | None) -> bool:
    return bool(chat and chat.get("guest"))


def chat_has_event_rules(chat: dict | None) -> bool:
    return bool(chat and chat.get("guest"))


def chat_can(chat: dict | None, action: str) -> bool:
    if not chat_has_action_rules(chat):
        return True
    if action.startswith("menu_"):
        group = _GROUP_ACTIONS.get(action[5:], set())
        return any(chat_can(chat, code) for code in group)
    actions = clean_guest_actions(chat.get("actions"))
    return bool(actions.get(action, False))


def chat_event_on(chat: dict | None, code: str) -> bool:
    if not chat_has_event_rules(chat):
        return True
    if code in {"report", "day_report"}:
        return chat_can(chat, "day_report")
    if code == "summary":
        return chat_can(chat, "summary")
    item = clean_guest_events(chat.get("events")).get(code) or {}
    return bool(item.get("on"))


def chat_event_sound(settings: dict | None, chat: dict | None, code: str) -> bool:
    if chat_has_event_rules(chat):
        item = clean_guest_events(chat.get("events")).get(code) or {}
        return bool(item.get("on") and item.get("sound"))
    return event_pref(settings, code)[1]


def chat_gets_event(settings: dict | None, chat: dict | None, code: str, force: bool = False) -> bool:
    if chat_has_event_rules(chat):
        return chat_event_on(chat, code)
    if force:
        return True
    send, _sound = event_pref(settings, code)
    return send


def extract_pair_code(text: str) -> str:
    raw = str(text or "").strip()
    if raw.lower().startswith("/start"):
        parts = raw.split(maxsplit=1)
        raw = parts[1].strip() if len(parts) > 1 else ""
    raw = raw.strip().strip("`")
    if re.fullmatch(r"\d{4,8}", raw):
        return raw
    return ""


def chat_is_admin(chat: dict | None) -> bool:
    return bool(chat and chat.get("admin") and not chat.get("guest"))


def global_event_map(settings: dict | None) -> dict:
    src = settings or {}
    return {
        code: {"on": event_pref(src, code)[0], "sound": event_pref(src, code)[1]}
        for code in EVENT_CODES
    }


def merge_global_events(settings: dict | None, patch) -> dict:
    src = settings or {}
    stored = src.get("telegram_events") if isinstance(src.get("telegram_events"), dict) else {}
    merged = {}
    for code in EVENT_CODES:
        send, sound = event_pref(src, code)
        prev = stored.get(code) if isinstance(stored.get(code), dict) else {}
        merged[code] = {
            "on": send,
            "sound": sound,
            "tray": bool(prev.get("tray", default_event_tray(code))),
        }
    if not isinstance(patch, dict):
        return merged
    for code, item in patch.items():
        if code not in EVENT_CODES:
            continue
        if isinstance(item, dict):
            if "on" in item:
                merged[code]["on"] = bool(item.get("on"))
            if "sound" in item:
                merged[code]["sound"] = bool(item.get("sound"))
            if "tray" in item:
                merged[code]["tray"] = bool(item.get("tray"))
        else:
            merged[code]["on"] = bool(item)
        if not merged[code]["on"]:
            merged[code]["sound"] = False
    return merged


_ALERTS = {
    "on_battery": True,
    "on_battery_clear": True,
    "battery_low": True,
    "battery_low_clear": True,
    "fault": True,
    "fault_clear": True,
    "link_lost": True,
    "link": True,
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

EVENT_CODES = frozenset(_ALERTS) | {"startup", "shutdown"}

EVENT_GROUPS = (
    ("Сеть", (
        "on_battery", "on_battery_clear", "voltage_low", "voltage_low_clear",
        "voltage_high", "voltage_high_clear", "voltage_crit_low", "voltage_crit_low_clear",
        "voltage_crit_high", "voltage_crit_high_clear",
    )),
    ("Батарея", (
        "battery_low", "battery_low_clear", "battery_loose", "battery_loose_clear", "battery_swap",
    )),
    ("ИБП", ("fault", "fault_clear", "link_lost", "link")),
    ("ПК", ("pc_shutdown", "pc_off", "pc_reboot", "pc_sleep", "pc_hibernate")),
    ("Программа", ("startup", "shutdown", "app_restart")),
)

ACTION_GROUPS = (
    ("ПК", ("pc_off", "pc_reboot", "pc_sleep", "pc_hibernate", "cancel_shutdown")),
    ("ПО", ("screenshot", "app_restart")),
    ("Отчёты", ("status", "events", "day_report", "summary")),
)

EVENT_TITLES = {
    "on_battery": "Сеть пропала",
    "on_battery_clear": "Сеть вернулась",
    "voltage_low": "Сеть понижена",
    "voltage_low_clear": "Понижение снято",
    "voltage_high": "Сеть повышена",
    "voltage_high_clear": "Повышение снято",
    "voltage_crit_low": "Сеть слишком низкая",
    "voltage_crit_low_clear": "Спад снят",
    "voltage_crit_high": "Сеть слишком высокая",
    "voltage_crit_high_clear": "Скачок снят",
    "battery_low": "Батарея садится",
    "battery_low_clear": "Батарея в норме",
    "battery_loose": "Батареи отключены",
    "battery_loose_clear": "Пакет в норме",
    "battery_swap": "Пакет сменился",
    "fault": "Неисправность",
    "fault_clear": "Авария снята",
    "link_lost": "Нет связи с ИБП",
    "link": "ИБП на связи",
    "pc_shutdown": "Отсчёт выключения",
    "pc_off": "Выключить ПК",
    "pc_reboot": "Перезагрузка",
    "pc_sleep": "Сон",
    "pc_hibernate": "Гибернация",
    "startup": "UPS запущен",
    "shutdown": "UPS закрыт",
    "app_restart": "Перезапуск UPS",
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
        targets = [item for item in chats if chat_gets_event(settings, item, "shutdown")]
        if not targets:
            return
        stamp = datetime.now().strftime("%H:%M")
        text = f"UPS закрыт в {stamp}"
        done = threading.Event()

        def work() -> None:
            try:
                png = notify_image("shutdown", card_title("shutdown", text), text)
                limit = max(1.0, float(timeout) / max(1, len(targets)))
                for item in targets:
                    chat_id = item.get("id")
                    try:
                        _send_photo(
                            token, chat_id, png, text,
                            menu=self._board(chat_id), chat=item, timeout=limit, silent=not sound,
                        )
                    except TelegramError:
                        try:
                            _send(
                                token, chat_id, text,
                                menu=self._board(chat_id), chat=item, timeout=limit, silent=not sound,
                            )
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
                    public_chat(item)
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
        if not force and code not in _ALERTS:
            return
        text = format_alert(event, reading)
        self._broadcast_notice(str(code), text, force=force)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                with self._lock:
                    self._error = str(exc)[:160]
                self._stop.wait(3)

    def _poll_once(self) -> None:
        token = self._token
        if not token:
            self._stop.wait(1.0)
            return
        if not self._ok:
            if not self._hello(token):
                self._stop.wait(8)
                return
            if not self._backlog_cleared:
                self._backlog_cleared = self._skip_pending(token)
        try:
            self._announce_start()
            self._maybe_digest()
        except Exception as exc:
            with self._lock:
                self._error = str(exc)[:160]
        try:
            data = _api(token, "getUpdates", {
                "timeout": 25,
                "offset": self._offset,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=35)
        except TelegramError as exc:
            self._on_poll_error(exc)
            return
        for update in data.get("result") or []:
            self._offset = int(update.get("update_id", 0)) + 1
            _save_offset(self._offset)
            try:
                self._handle(update)
            except Exception as exc:
                with self._lock:
                    self._error = str(exc)[:160]

    def _on_poll_error(self, exc: TelegramError) -> None:
        text = str(exc)
        with self._lock:
            self._error = text[:160]
            if poll_error_fatal(text):
                self._ok = False
        wait = 3.0 if "conflict" in text.lower() else 2.0
        if poll_error_fatal(text):
            wait = 8.0
        self._stop.wait(wait)

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
        if not self._known(int(chat_id)):
            self._pair_or_ask(int(chat_id), name, text)
            return
        chat = self._chat_item(chat_id)
        lowered = _plain(text)
        if lowered in {"чаты", "чат"}:
            if not chat_is_admin(chat):
                _send(self._token, chat_id, "Управлять чатами может только админ.", menu=self._board(chat_id), chat=chat)
                return
            self._admin_home(chat_id)
            return
        if lowered in {"пк", "компьютер"}:
            if not chat_can(chat, "menu_pc"):
                self._deny(chat_id)
                return
            self._open_menu(chat_id, "pc", "Питание компьютера.")
            return
        if lowered in {"по", "программа"}:
            if not chat_can(chat, "menu_app"):
                self._deny(chat_id)
                return
            self._open_menu(chat_id, "app", "Программа UPS.")
            return
        if lowered in {"отчёты", "отчеты"}:
            if not chat_can(chat, "menu_reports"):
                self._deny(chat_id)
                return
            self._open_menu(chat_id, "reports", "Отчёты и журнал.")
            return
        if lowered in {"назад", "меню"}:
            self._open_menu(chat_id, "root", "Главное меню.")
            return
        head = lowered.split()[0] if lowered else ""
        if lowered in {"состояние", "status", "запрос состояния"} or head in {"состояние", "status"}:
            if not chat_can(chat, "status"):
                self._deny(chat_id)
                return
            self._notice(chat_id, "status", self._host.telegram_status_text())
            return
        if lowered in {"отчёт дня", "отчет дня", "отчёт", "отчет", "report"}:
            if not chat_can(chat, "day_report"):
                self._deny(chat_id)
                return
            self._send_html(chat_id, "day_report", *self._host.telegram_day_html())
            return
        if lowered in {"сводка", "сводка дней", "summary"}:
            if not chat_can(chat, "summary"):
                self._deny(chat_id)
                return
            self._send_html(chat_id, "summary", *self._host.telegram_summary_html())
            return
        if lowered in {"неделя", "week"}:
            if not chat_can(chat, "day_report"):
                self._deny(chat_id)
                return
            self._notice(chat_id, "report", self._host.telegram_report_text(24 * 7))
            return
        if lowered in {"журнал", "events"} or head in {"журнал", "events"}:
            if not chat_can(chat, "events"):
                self._deny(chat_id)
                return
            self._notice(chat_id, "events", self._host.telegram_events_text())
            return
        if lowered in {"скриншот", "screenshot", "экран"} or head in {"скриншот", "screenshot", "экран"}:
            if not chat_can(chat, "screenshot"):
                self._deny(chat_id)
                return
            threading.Thread(target=self._send_screenshot, args=(int(chat_id),), daemon=True).start()
            return
        if lowered in {"перезапуск программы", "перезагрузить программу", "рестарт"}:
            if not chat_can(chat, "app_restart"):
                self._deny(chat_id)
                return
            self._ask_power(chat_id, "app_restart")
            return
        if lowered in {"отмена выключения", "cancel", "отмена"}:
            if not chat_can(chat, "cancel_shutdown"):
                self._deny(chat_id)
                return
            result = self._host.command("cancel_shutdown")
            self._notice(chat_id, "cancel", result.get("message") or "Готово")
            return
        if lowered in {"помощь", "help", "start"}:
            self._notice(chat_id, "help", help_text(self._username))
            return
        if lowered in _POWER:
            action = _POWER[lowered]
            if not chat_can(chat, action):
                self._deny(chat_id)
                return
            self._ask_power(chat_id, action)
            return
        _send(self._token, chat_id, "Не понял. Нажмите кнопку внизу или /help.", menu=self._board(chat_id), chat=chat)

    def _pair_or_ask(self, chat_id: int, name: str, text: str) -> None:
        settings = self._host.get_settings_raw()
        pair = str(settings.get("telegram_pair") or "")
        code = extract_pair_code(text)
        if pair and code == pair:
            self._host.link_telegram_chat({"id": chat_id, "name": name})
            self._open_menu(chat_id, "root", f"Готово. Этот чат получает сообщения UPS.\n\n{help_text(self._username)}")
            return
        _send(
            self._token,
            chat_id,
            "Напишите код из вкладки Телеграм в UPS. Только цифры, без /start.",
        )

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
                chat=self._chat_item(chat_id),
            )
        except TelegramError as exc:
            self._notice(chat_id, code, f"HTML не ушёл: {exc}")

    def _send_screenshot(self, chat_id: int) -> None:
        png = self._host.telegram_screenshot()
        if not png:
            self._notice(chat_id, "screenshot", "Не удалось снять главное окно. UPS должен быть запущен.")
            return
        try:
            _send_photo(self._token, chat_id, png, "Главное окно UPS", menu=self._board(chat_id), chat=self._chat_item(chat_id))
        except TelegramError as exc:
            _send(self._token, chat_id, f"Скриншот не ушёл: {exc}", menu=self._board(chat_id), chat=self._chat_item(chat_id))

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
        if chat_id is None:
            return
        if data.startswith("a:"):
            if not self._known(int(chat_id)) or not chat_is_admin(self._chat_item(chat_id)):
                self._deny(chat_id)
                return
            if not callback_fresh(query, max_age=900):
                _send(self._token, chat_id, "Список устарел. Откройте Чаты снова.", menu=self._board(chat_id), chat=self._chat_item(chat_id))
                return
            self._admin_callback(int(chat_id), data, query)
            return
        if not self._known(int(chat_id)):
            return
        if not callback_fresh(query):
            self._notice(chat_id, "cancel", "Кнопка устарела. Нажмите действие ещё раз.")
            return
        action, _, answer = data.partition(":")
        if answer != "yes" or action not in _CONFIRM:
            self._notice(chat_id, "cancel", "Отменено.")
            return
        if not chat_can(self._chat_item(chat_id), action):
            self._deny(chat_id)
            return
        if action == "app_restart":
            self._ack_offset()
        result = self._host.command(action)
        if action != "app_restart":
            self._notice(chat_id, action, result.get("message") or "Готово")

    def _known(self, chat_id: int) -> bool:
        return self._chat_item(chat_id) is not None

    def _chat_item(self, chat_id) -> dict | None:
        return find_chat(self._host.get_settings_raw(), chat_id)

    def _deny(self, chat_id) -> None:
        _send(
            self._token,
            chat_id,
            "Это действие выключено для гостевого чата.",
            menu=self._board(chat_id),
            chat=self._chat_item(chat_id),
        )

    def _board(self, chat_id) -> str:
        return self._boards.get(int(chat_id), "root")

    def _open_menu(self, chat_id, name: str, text: str) -> None:
        self._boards[int(chat_id)] = name
        _send(self._token, chat_id, text, menu=name, chat=self._chat_item(chat_id))

    def _admin_home(self, chat_id, query: dict | None = None) -> None:
        settings = self._host.get_settings_raw()
        rows = []
        for item in settings.get("telegram_chats") or []:
            if not isinstance(item, dict):
                continue
            pub = public_chat(item)
            mine = int(item.get("id") or 0) == int(chat_id)
            role = "гость" if pub["guest"] else "админ" if pub["admin"] else "полный"
            mark = "вы · " if mine else ""
            rows.append([{
                "text": f"{mark}{pub['name'] or pub['id']} · {role}",
                "callback_data": f"a:o:{pub['id']}",
            }])
        if not rows:
            rows = [[{"text": "Чатов нет", "callback_data": "a:l"}]]
        self._admin_show(chat_id, query, "Чаты. Свой профиль — общие правила. Роль себе снять нельзя.", rows)

    def _admin_open(self, actor_id: int, target_id: int, query: dict | None = None) -> None:
        target = find_chat(self._host.get_settings_raw(), target_id)
        if target is None:
            self._admin_home(actor_id, query)
            return
        pub = public_chat(target)
        mine = int(target_id) == int(actor_id)
        if mine:
            self._admin_show(actor_id, query, "Ваш профиль · админ\nЗдесь общие уведомления бота. Свою роль снять нельзя.", [
                [{"text": "Уведомления", "callback_data": f"a:eg:{target_id}:0"}],
                [{"text": "Назад", "callback_data": "a:l"}],
            ])
            return
        role = "гость" if pub["guest"] else "админ" if pub["admin"] else "полный доступ"
        text = f"{pub['name'] or pub['id']}\nСейчас: {role}"
        if pub["admin"]:
            text += "\nУведомления этого чата — общие."
        rows = [
            [
                {"text": "Полный", "callback_data": f"a:r:{target_id}:full"},
                {"text": "Гость", "callback_data": f"a:r:{target_id}:guest"},
                {"text": "Админ", "callback_data": f"a:r:{target_id}:admin"},
            ],
        ]
        if pub["guest"]:
            rows.append([
                {"text": "Уведомления", "callback_data": f"a:eg:{target_id}:0"},
                {"text": "Кнопки", "callback_data": f"a:ag:{target_id}:0"},
            ])
        rows.append([{"text": "Отвязать", "callback_data": f"a:u:{target_id}"}])
        rows.append([{"text": "Назад", "callback_data": "a:l"}])
        self._admin_show(actor_id, query, text, rows)

    def _admin_events(self, actor_id: int, target_id: int, group: int, query: dict | None = None) -> None:
        target = find_chat(self._host.get_settings_raw(), target_id)
        if target is None:
            self._admin_open(actor_id, target_id, query)
            return
        group = max(0, min(int(group), len(EVENT_GROUPS) - 1))
        title, codes = EVENT_GROUPS[group]
        mine = int(target_id) == int(actor_id)
        if mine:
            events = global_event_map(self._host.get_settings_raw())
            text = f"Общие уведомления · {title}"
        elif target.get("guest"):
            events = public_chat(target).get("events") or clean_guest_events(target.get("events"))
            text = f"{target.get('name') or target_id}\nУведомления · {title}"
        else:
            self._admin_open(actor_id, target_id, query)
            return
        rows = []
        for code in codes:
            item = events.get(code) or {"on": False, "sound": False}
            mark = "✓" if item.get("on") else "—"
            bell = "🔊" if item.get("sound") else "🔇"
            rows.append([
                {"text": f"{mark} {EVENT_TITLES.get(code, code)}", "callback_data": f"a:e:{target_id}:{code}:on"},
                {"text": bell, "callback_data": f"a:e:{target_id}:{code}:snd"},
            ])
        rows.append([
            {"text": "Все вкл", "callback_data": f"a:egon:{target_id}:{group}:1"},
            {"text": "Все выкл", "callback_data": f"a:egon:{target_id}:{group}:0"},
        ])
        nav = []
        if group > 0:
            nav.append({"text": "←", "callback_data": f"a:eg:{target_id}:{group - 1}"})
        if group < len(EVENT_GROUPS) - 1:
            nav.append({"text": "→", "callback_data": f"a:eg:{target_id}:{group + 1}"})
        nav.append({"text": "Назад", "callback_data": f"a:o:{target_id}"})
        rows.append(nav)
        self._admin_show(actor_id, query, text, rows)

    def _admin_actions(self, actor_id: int, target_id: int, group: int, query: dict | None = None) -> None:
        target = find_chat(self._host.get_settings_raw(), target_id)
        if target is None:
            self._admin_open(actor_id, target_id, query)
            return
        group = max(0, min(int(group), len(ACTION_GROUPS) - 1))
        title, codes = ACTION_GROUPS[group]
        actions = public_chat(target).get("actions") or clean_guest_actions(target.get("actions"))
        titles = {item["code"]: item["title"] for item in BOT_ACTIONS}
        text = f"{target.get('name') or target_id}\nКнопки · {title}"
        rows = []
        for code in codes:
            mark = "✓" if actions.get(code) else "—"
            rows.append([{
                "text": f"{mark} {titles.get(code, code)}",
                "callback_data": f"a:a:{target_id}:{code}",
            }])
        rows.append([
            {"text": "Все вкл", "callback_data": f"a:agon:{target_id}:{group}:1"},
            {"text": "Все выкл", "callback_data": f"a:agon:{target_id}:{group}:0"},
        ])
        nav = []
        if group > 0:
            nav.append({"text": "←", "callback_data": f"a:ag:{target_id}:{group - 1}"})
        if group < len(ACTION_GROUPS) - 1:
            nav.append({"text": "→", "callback_data": f"a:ag:{target_id}:{group + 1}"})
        nav.append({"text": "Назад", "callback_data": f"a:o:{target_id}"})
        rows.append(nav)
        self._admin_show(actor_id, query, text, rows)

    def _admin_callback(self, actor_id: int, data: str, query: dict) -> None:
        parts = data.split(":")
        kind = parts[1] if len(parts) > 1 else "l"
        if kind == "l":
            self._admin_home(actor_id, query)
            return
        try:
            target_id = int(parts[2])
        except (IndexError, TypeError, ValueError):
            self._admin_home(actor_id, query)
            return
        if kind == "o":
            self._admin_open(actor_id, target_id, query)
            return
        if kind == "u":
            if int(target_id) == int(actor_id):
                self._admin_open(actor_id, target_id, query)
                return
            self._admin_show(actor_id, query, "Отвязать этот чат?", [[
                {"text": "Да, отвязать", "callback_data": f"a:U:{target_id}"},
                {"text": "Нет", "callback_data": f"a:o:{target_id}"},
            ]])
            return
        if kind == "U":
            if int(target_id) == int(actor_id):
                self._admin_open(actor_id, target_id, query)
                return
            self._host.update_telegram_chat({"id": target_id, "action": "unlink", "actor": actor_id})
            self._admin_home(actor_id, query)
            return
        if kind == "r":
            if int(target_id) == int(actor_id):
                self._admin_open(actor_id, target_id, query)
                return
            role = parts[3] if len(parts) > 3 else "full"
            payload = {"id": target_id, "actor": actor_id, "admin": role == "admin", "guest": role == "guest"}
            self._host.update_telegram_chat(payload)
            self._admin_open(actor_id, target_id, query)
            return
        target = find_chat(self._host.get_settings_raw(), target_id)
        if target is None:
            self._admin_open(actor_id, target_id, query)
            return
        if kind == "eg":
            self._admin_events(actor_id, target_id, int(parts[3]) if len(parts) > 3 else 0, query)
            return
        if kind == "ag":
            if int(target_id) == int(actor_id):
                self._admin_open(actor_id, target_id, query)
                return
            self._admin_actions(actor_id, target_id, int(parts[3]) if len(parts) > 3 else 0, query)
            return
        mine = int(target_id) == int(actor_id)
        role = {
            "admin": bool(target.get("admin") and not target.get("guest")),
            "guest": bool(target.get("guest")),
        }
        if kind == "e" and len(parts) >= 5:
            if mine:
                events = global_event_map(self._host.get_settings_raw())
            elif target.get("guest"):
                events = public_chat(target).get("events") or clean_guest_events(target.get("events"))
            else:
                self._admin_open(actor_id, target_id, query)
                return
            code = parts[3]
            item = dict(events.get(code) or {"on": False, "sound": False})
            if parts[4] == "snd":
                item["sound"] = not item.get("sound")
                if item["sound"]:
                    item["on"] = True
            else:
                item["on"] = not item.get("on")
                if not item["on"]:
                    item["sound"] = False
            events[code] = item
            if mine:
                self._host.update_global_telegram_events(events, actor_id)
            else:
                self._host.update_telegram_chat({"id": target_id, "actor": actor_id, **role, "events": events})
            group = next((i for i, pack in enumerate(EVENT_GROUPS) if code in pack[1]), 0)
            self._admin_events(actor_id, target_id, group, query)
            return
        if kind == "egon" and len(parts) >= 5:
            group = int(parts[3])
            on = parts[4] == "1"
            if mine:
                events = global_event_map(self._host.get_settings_raw())
            elif target.get("guest"):
                events = public_chat(target).get("events") or clean_guest_events(target.get("events"))
            else:
                self._admin_open(actor_id, target_id, query)
                return
            for code in EVENT_GROUPS[group][1]:
                events[code] = {"on": on, "sound": on and default_event_sound(code)}
            if mine:
                self._host.update_global_telegram_events(events, actor_id)
            else:
                self._host.update_telegram_chat({"id": target_id, "actor": actor_id, **role, "events": events})
            self._admin_events(actor_id, target_id, group, query)
            return
        if mine:
            self._admin_open(actor_id, target_id, query)
            return
        if kind == "a" and len(parts) >= 4:
            actions = public_chat(target).get("actions") or clean_guest_actions(target.get("actions"))
            code = parts[3]
            actions[code] = not actions.get(code)
            self._host.update_telegram_chat({"id": target_id, "actor": actor_id, **role, "actions": actions})
            if int(actor_id) == int(target_id):
                self._refresh_menu(actor_id)
            group = next((i for i, pack in enumerate(ACTION_GROUPS) if code in pack[1]), 0)
            self._admin_actions(actor_id, target_id, group, query)
            return
        if kind == "agon" and len(parts) >= 5:
            group = int(parts[3])
            on = parts[4] == "1"
            actions = public_chat(target).get("actions") or clean_guest_actions(target.get("actions"))
            for code in ACTION_GROUPS[group][1]:
                actions[code] = on
            self._host.update_telegram_chat({"id": target_id, "actor": actor_id, **role, "actions": actions})
            if int(actor_id) == int(target_id):
                self._refresh_menu(actor_id)
            self._admin_actions(actor_id, target_id, group, query)
            return
        self._admin_open(actor_id, target_id, query)

    def _refresh_menu(self, chat_id) -> None:
        chat = self._chat_item(chat_id)
        if not chat or not self._token:
            return
        try:
            _send(self._token, chat_id, "Правила обновлены.", menu=self._board(chat_id), chat=chat)
        except TelegramError:
            pass

    def _admin_show(self, chat_id, query: dict | None, text: str, rows: list) -> None:
        markup = {"inline_keyboard": rows}
        message = (query or {}).get("message") or {}
        mid = message.get("message_id")
        if mid:
            try:
                _api(self._token, "editMessageText", {
                    "chat_id": chat_id,
                    "message_id": mid,
                    "text": text[:3900],
                    "reply_markup": markup,
                }, timeout=8)
                return
            except TelegramError:
                pass
        _send(self._token, chat_id, text, inline=rows, menu=None)

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
        chat = self._chat_item(chat_id)
        try:
            _send_photo(token, chat_id, png, text[:1024], inline=inline, menu=self._board(chat_id), chat=chat, silent=silent)
            if len(text) > 1024:
                _send(token, chat_id, text[1024:], menu=None if inline else self._board(chat_id), chat=chat, silent=silent)
        except TelegramError:
            _send(token, chat_id, text, menu=None if inline else self._board(chat_id), chat=chat, inline=inline, silent=silent)

    def _broadcast_notice(self, code: str, text: str, force: bool = False) -> None:
        token = self._token
        if not token or not text:
            return
        settings = self._host.get_settings_raw()
        send, sound = event_pref(settings, code)
        silent = not sound
        sent = False
        for item in settings.get("telegram_chats") or []:
            if not isinstance(item, dict):
                continue
            chat_id = item.get("id")
            if chat_id is None:
                continue
            if not chat_gets_event(settings, item, code, force=force):
                continue
            if not chat_has_event_rules(item) and not send and not force:
                continue
            try:
                self._notice(chat_id, code, text, silent=not chat_event_sound(settings, item, code))
                sent = True
            except TelegramError as exc:
                with self._lock:
                    self._error = str(exc)
        if not sent and not send and not force:
            return

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
                _send(token, chat_id, text, menu="root", chat=item)
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


def poll_error_fatal(text: str) -> bool:
    lowered = str(text or "").lower()
    return "unauthorized" in lowered or "invalid token" in lowered or "webhook" in lowered


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
        "Слева от поля ввода — состояние, скриншот и журнал.\n"
        "Новый чат привязывается кодом из программы, без /start."
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
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
    except TelegramError:
        raise
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
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise TelegramError(str(exc)[:160] or "сбой Telegram") from exc
    if not data.get("ok"):
        raise TelegramError(str(data.get("description") or "ответ Telegram без ok")[:160])
    return data


def _menu_rows(name: str | None, chat: dict | None = None) -> list[list[str]]:
    key = name or "root"
    if key == "root" and chat_is_admin(chat):
        raw = _MENUS["root_admin"]
    else:
        raw = _MENUS.get(key) or _MENUS["root"]
    if not chat or not chat.get("guest"):
        return raw
    filtered = []
    for row in raw:
        kept = [cell for cell in row if _button_allowed(cell, chat)]
        if kept:
            filtered.append(kept)
    if filtered:
        return filtered
    if name and name != "root":
        return [["Назад"]]
    if chat_can(chat, "status"):
        return [["Состояние"]]
    return [["Назад"]]


def _button_allowed(cell: str, chat: dict | None) -> bool:
    if cell == "Назад":
        return True
    action = _BUTTON_ACTION.get(cell)
    if not action:
        return True
    return chat_can(chat, action)


def _keyboard(menu: str | None, chat: dict | None = None) -> dict:
    return {
        "keyboard": [[{"text": cell} for cell in row] for row in _menu_rows(menu, chat)],
        "resize_keyboard": True,
    }


def _send(token: str, chat_id, text: str, menu: str | None = None, inline: list | None = None, timeout: float = 12, silent: bool = False, chat: dict | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
        "disable_notification": bool(silent),
    }
    if inline:
        payload["reply_markup"] = {"inline_keyboard": [inline] if inline and isinstance(inline[0], dict) else inline}
    elif menu:
        payload["reply_markup"] = _keyboard(menu, chat)
    _api(token, "sendMessage", payload, timeout=timeout)


def _send_photo(token: str, chat_id, png: bytes, caption: str = "", inline: list | None = None, menu: str | None = "root", timeout: float = 20, silent: bool = False, chat: dict | None = None) -> None:
    boundary = "----UPSFormBoundary7"
    if inline:
        markup = json.dumps({
            "inline_keyboard": [inline] if inline and isinstance(inline[0], dict) else inline,
        }, ensure_ascii=False)
    else:
        markup = json.dumps(_keyboard(menu, chat), ensure_ascii=False)
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


def _send_document(token: str, chat_id, body: bytes, filename: str, caption: str = "", menu: str | None = "root", chat: dict | None = None) -> None:
    boundary = "----UPSFormBoundary8"
    markup = json.dumps(_keyboard(menu, chat), ensure_ascii=False)
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
