import threading
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from ups.monitor import SIMULATIONS, _decimate, _public_settings, _validate
from ups.notify_art import _fit_title, card_title, notify_image
from ups.telegram import (
    TOKEN_RE,
    TelegramBridge,
    TelegramError,
    _api,
    _menu_rows,
    callback_fresh,
    chat_can,
    chat_event_sound,
    chat_gets_event,
    chat_is_admin,
    default_guest_events,
    merge_global_events,
    digest_due,
    event_pref,
    extract_pair_code,
    format_alert,
    compose_live_markdown,
    shift_live_deadline,
    format_events,
    format_day_report,
    format_live_markdown,
    format_report,
    split_telegram_text,
    parse_clock,
    poll_error_fatal,
    public_chat,
    status_lines,
    tray_pref,
    windows_notify_allowed,
)
from ups.tray import Tray


class TokenTests(unittest.TestCase):
    def test_accepts_botfather_shape(self):
        self.assertTrue(TOKEN_RE.fullmatch("123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx"))

    def test_rejects_junk(self):
        self.assertFalse(TOKEN_RE.fullmatch("not-a-token"))
        clean, error = _validate({"telegram_token": "abc"})
        self.assertIn("Токен", error or "")
        self.assertEqual(clean, {})

    def test_live_timeout_setting_defaults_and_validates(self):
        from ups.monitor import DEFAULTS
        self.assertEqual(DEFAULTS["telegram_live_timeout"], 10)
        self.assertEqual(DEFAULTS["telegram_live_interval"], 3)
        clean, error = _validate({
            "telegram_live_timeout": "15",
            "telegram_live_interval": "5",
        })
        self.assertIsNone(error)
        self.assertEqual(clean["telegram_live_timeout"], 15)
        self.assertEqual(clean["telegram_live_interval"], 5)
        clean, error = _validate({"telegram_live_timeout": 0})
        self.assertEqual(clean, {})
        self.assertIn("telegram_live_timeout", error or "")
        clean, error = _validate({"telegram_live_interval": 0})
        self.assertEqual(clean, {})
        self.assertIn("telegram_live_interval", error or "")

    def test_digest_time_round_trip(self):
        self.assertEqual(parse_clock("8:05"), "08:05")
        self.assertEqual(parse_clock("21:00:00"), "21:00")
        self.assertIsNone(parse_clock("25:00"))
        clean, error = _validate({"telegram_digest": True, "telegram_digest_time": "07:30"})
        self.assertIsNone(error)
        self.assertEqual(clean["telegram_digest_time"], "07:30")
        now = datetime(2026, 9, 27, 7, 32)
        self.assertTrue(digest_due(now, "07:30"))
        self.assertFalse(digest_due(now, "21:00"))

    def test_event_pref_and_chart_settings(self):
        send, sound = event_pref({"telegram_events": {"fault": {"on": True, "sound": False}}}, "fault")
        self.assertTrue(send)
        self.assertFalse(sound)
        send, sound = event_pref({"telegram_events": {"fault": {"on": False, "sound": True}}}, "fault")
        self.assertFalse(send)
        send, sound = event_pref({}, "voltage_low_clear")
        self.assertTrue(send)
        self.assertTrue(sound)
        send, sound = event_pref({
            "telegram_events": {
                "voltage_low": {"on": True, "sound": True, "tray": True},
                "voltage_low_clear": {"on": True, "sound": False, "tray": False},
                "voltage_high_clear": {"on": False, "sound": False, "tray": False},
            }
        }, "voltage_low_clear")
        self.assertTrue(send)
        self.assertTrue(sound)
        send, sound = event_pref({
            "telegram_events": {"voltage_high_clear": {"on": False, "sound": False}}
        }, "voltage_high_clear")
        self.assertFalse(send)
        clean, error = _validate({
            "spark_lines": ["in", "load", "nope"],
            "spark_window": 1800,
            "chart_minutes": 360,
            "chart_series": "charge",
            "telegram_events": {"on_battery": {"on": True, "sound": False}, "unknown": {"on": True}},
        })
        self.assertIsNone(error)
        self.assertEqual(clean["spark_lines"], ["in", "load"])
        self.assertEqual(clean["spark_window"], 1800)
        self.assertEqual(clean["chart_minutes"], 360)
        self.assertEqual(clean["chart_series"], "charge")
        mapped, err = _validate({"chart_series": "battery"})
        self.assertIsNone(err)
        self.assertEqual(mapped["chart_series"], "charge")
        self.assertEqual(
            clean["telegram_events"]["on_battery"],
            {"on": True, "sound": False, "tray": True},
        )
        self.assertNotIn("unknown", clean["telegram_events"])
        self.assertTrue(tray_pref({}, "on_battery"))
        self.assertFalse(tray_pref({}, "on_battery_clear"))
        self.assertFalse(tray_pref(
            {"telegram_events": {"fault": {"on": True, "sound": True, "tray": False}}},
            "fault",
        ))
        self.assertTrue(windows_notify_allowed({}, "on_battery"))
        self.assertFalse(windows_notify_allowed({"windows_notify": False}, "on_battery"))
        clean_win, error = _validate({"windows_notify": False})
        self.assertIsNone(error)
        self.assertFalse(clean_win["windows_notify"])
        geom, error = _validate({
            "window_x": -80,
            "window_y": 40,
            "window_width": 1400,
            "window_height": 900,
            "window_maximized": True,
        })
        self.assertIsNone(error)
        self.assertEqual(geom["window_x"], -80)
        self.assertEqual(geom["window_width"], 1400)
        self.assertTrue(geom["window_maximized"])


class ReportTests(unittest.TestCase):
    def test_status_mentions_mains(self):
        lines = status_lines({
            "mains_ok": False,
            "input_v": 0,
            "output_v": 215.2,
            "load_pct": 36,
            "load_w": 252,
            "battery_v": 24.8,
            "battery_pct": 81,
            "runtime_min": 22,
            "on_battery": True,
            "discharge_seconds": 95,
            "mode_label": "Питание от батареи",
        })
        text = "\n".join(lines)
        self.assertIn("Сеть: нет", text)
        self.assertIn("215.2", text)
        self.assertIn("≈ 252 Вт", text)
        self.assertIn("≈ 22 мин", text)

    def test_status_mentions_low_mains(self):
        lines = status_lines({
            "mains_ok": True,
            "input_v": 192,
            "output_v": 220,
            "voltage_state": "low",
            "voltage_note": "Вход ниже 195 В",
        })
        self.assertIn("Вход ниже 195 В", "\n".join(lines))

    def test_status_mentions_pack_swap(self):
        lines = status_lines({
            "mains_ok": True,
            "input_v": 220,
            "output_v": 220,
            "load_pct": 20,
            "battery_v": 24.1,
            "battery_pct": 70,
            "battery_swap": {"from": 26.6, "to": 24.1},
        })
        self.assertIn("26.6 → 24.1 В", "\n".join(lines))

    def test_full_day_report_includes_every_event_and_markdown(self):
        events = [
            {"t": 1710000000 + index, "code": "fault", "message": f"Событие_{index}"}
            for index in range(12)
        ]
        text = format_day_report({
            "date": "2026-11-04",
            "generated": "2026-11-04 12:00",
            "stats": {"events": 12, "outages": 2, "energyKwh": 1.25, "peakW": 420},
            "events": events,
        })
        self.assertIn("*Суточный отчёт", text)
        self.assertIn("Пиковая мощность", text)
        self.assertIn("Событие\\_0", text)
        self.assertIn("Событие\\_11", text)
        self.assertEqual(text.count(chr(96)), 24)
        chunks = split_telegram_text("строка\n" * 1000, limit=3900)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 3900 for chunk in chunks))

    def test_live_session_pins_and_unpins_one_message(self):
        class Host:
            def get_settings_raw(self):
                return {
                    "telegram_live_timeout": 10,
                    "telegram_live_interval": 5,
                    "telegram_chats": [{"id": 7, "name": "test"}],
                }

            def telegram_live_text(self):
                return "UPS · ЖИВАЯ ТРАНСЛЯЦИЯ"

        calls = []
        sent_ids = iter([77, 78])

        def api(_token, method, payload=None, timeout=12):
            calls.append((method, payload or {}))
            if method in {"sendMessage", "sendRichMessage"}:
                return {"result": {"message_id": next(sent_ids)}}
            return {"result": True}

        class DummyThread:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def start(self):
                pass

        bot = TelegramBridge(Host())
        bot._token = "123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        with patch("ups.telegram._api", side_effect=api), patch("ups.telegram.threading.Thread", DummyThread):
            bot._start_live(7)
            session = bot._live_sessions[7]
            started = session["started_at"]
            bot._start_live(7)
            self.assertIs(bot._live_sessions[7], session)
            self.assertEqual(session["started_at"], started)
            self.assertEqual(session["timeout"], 600)
            self.assertEqual(session["message_id"], 78)
            self.assertEqual(session["interval"], 5)
            sent = [payload for method, payload in calls if method == "sendRichMessage"]
            self.assertEqual(len(sent), 2)
            self.assertTrue(sent[1].get("disable_notification"))
            self.assertIn("UPS · ЖИВАЯ ТРАНСЛЯЦИЯ", sent[0]["rich_message"]["markdown"])
            labels = [
                button["callback_data"]
                for row in sent[0]["reply_markup"]["inline_keyboard"]
                for button in row
            ]
            self.assertEqual(labels, [
                "live:-1", "live:-5", "live:-30",
                "live:+1", "live:+5", "live:+30",
                "live:s1", "live:s5", "live:s10",
                "live:proto",
                "live:inf", "live:stop",
            ])
            self.assertEqual([method for method, _payload in calls].count("sendMessage"), 0)
            self.assertIn(("pinChatMessage", {"chat_id": 7, "message_id": 78, "disable_notification": True}), calls)
            self.assertIn(("deleteMessage", {"chat_id": 7, "message_id": 77}), calls)
            session["stop"].set()
            bot._live_loop(7, session)
        self.assertIn("unpinChatMessage", [method for method, _payload in calls])
        final = [payload for method, payload in calls if method == "editMessageText"][-1]
        self.assertIn("Трансляция завершена", final["rich_message"]["markdown"])
        self.assertEqual(final["reply_markup"], {"inline_keyboard": []})
        self.assertNotIn(7, bot._live_sessions)

    def test_live_markdown_is_a_rich_card(self):
        text = format_live_markdown({
            "mains_ok": False,
            "input_v": 0,
            "output_v": 215.2,
            "frequency": 50,
            "temperature": 31,
            "load_pct": 90,
            "load_w": 630,
            "battery_v": 24.1,
            "battery_pct": 15,
            "battery_wh": 40,
            "battery_wh_full": 240,
            "runtime_min": 8,
            "on_battery": True,
            "discharge_seconds": 95,
            "mode_label": "Работа от батареи",
            "ups_kind": "Онлайн",
            "voltage_state": "idle",
            "battery_low": True,
            "avr": False,
            "fault": False,
            "test": False,
            "shutdown": False,
            "beeper": True,
            "raw": "(000.0 000.0 215.2 090 50.0 24.1 31.0 10000001",
            "volt_limits": {"crit_low": 180, "low": 200, "high": 240, "crit_high": 250},
            "updated_at": 1762344552,
        }, "Связь есть")
        self.assertIn("# 🟠 UPS", text)
        self.assertIn("## 📡 Живая трансляция", text)
        self.assertIn("## 🔌 Сеть", text)
        self.assertIn("## 🪫 Батарея", text)
        self.assertIn("## ⚠️ Нагрузка", text)
        self.assertIn("tg://time?unix=1762344552&format=wDT", text)
        self.assertIn("<u>Работа от батареи</u>", text)
        self.assertIn("~~Сети нет~~", text)
        self.assertIn("| Выход |", text)
        self.assertIn("**0.0 В**", text)
        self.assertIn("| Энергия | **40 / 240 Вт·ч** |", text)
        self.assertIn("`█", text)
        self.assertIn("==15%==", text)
        self.assertIn("==90%==", text)
        self.assertIn("- [x] 🟠 Работа от батареи", text)
        self.assertIn("- [ ] 🔴 Сеть", text)
        self.assertNotIn("000.0", text)
        self.assertNotIn("<details", text)
        opened = format_live_markdown({
            "mains_ok": False,
            "raw": "(000.0 000.0 215.2 090 50.0 24.1 31.0 10000001",
            "volt_limits": {"crit_low": 180, "low": 200, "high": 240, "crit_high": 250},
        }, protocol_open=True)
        self.assertIn("## 📋 Протокол и пороги", opened)
        self.assertIn("000.0", opened)
        self.assertNotIn("<details", opened)
        card = compose_live_markdown(text, remaining=125, total=600, interval=3)
        self.assertIn("## ⏱ Сеанс", card)
        self.assertIn("осталось **2 мин 05 с**", card)
        self.assertIn("_Обновление каждые 3 с_", card)
        done = compose_live_markdown(text, finished="остановлена пользователем")
        self.assertIn("> **Трансляция завершена:** остановлена пользователем", done)
        self.assertIn('type="disabled"', done)
        self.assertNotIn("live:stop", done)
        escaped = format_live_markdown(None, "режим_1 *важно*")
        self.assertIn("режим\\_1 \\*важно\\*", escaped)
        self.assertIn("ИБП пока молчит.", escaped)
        endless = compose_live_markdown("карточка", infinite=True, interval=3)
        self.assertIn("**∞**", endless)
        self.assertNotIn("осталось", endless)

    def test_live_timer_buttons_have_no_settings_cap(self):
        session = {"started_at": time.monotonic() - 100, "timeout": 600}
        started = session["started_at"]
        for _ in range(4):
            shift_live_deadline(session, 30)
        self.assertEqual(session["started_at"], started)
        self.assertGreater(session["timeout"], 7200)
        shift_live_deadline(session, None)
        self.assertTrue(session["infinite"])
        frozen = session["frozen_remaining"]
        restored = shift_live_deadline(session, None)
        self.assertFalse(session.get("infinite"))
        self.assertAlmostEqual(restored, frozen, delta=1)
        shift_live_deadline(session, None)
        shift_live_deadline(session, 5)
        self.assertFalse(session["infinite"])
        self.assertAlmostEqual(session["timeout"], frozen + 300, delta=1)
        shift_live_deadline(session, -30)
        self.assertLess(session["timeout"], frozen)
        day = format_live_markdown({"mains_ok": True, "input_v": 220, "output_v": 220}, energy={
            "energyKwh": 1.25,
            "energyKwhMains": 1.1,
            "energyKwhBattery": 0.15,
            "avgW": 180,
            "peakW": 420,
        })
        self.assertIn("## 📈 Сутки", day)
        self.assertIn("⚡ сеть", day)
        self.assertIn("**1.25 кВт·ч**", day)
        self.assertIn("**0.15 кВт·ч**", day)
        self.assertIn("сеть", day)
        self.assertIn("420", day)

    def test_live_interval_button_updates_session(self):
        class Host:
            def telegram_live_text(self):
                return "карточка"

        bot = TelegramBridge(Host())
        bot._token = "123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        wake = threading.Event()
        bot._live_sessions[7] = {
            "token": bot._token,
            "message_id": 3,
            "started_at": time.monotonic(),
            "timeout": 600,
            "interval": 3,
            "pin_error": "",
            "stop": threading.Event(),
            "wake": wake,
            "reason": "",
        }
        with patch("ups.telegram._api", return_value={"result": True}):
            bot._set_live_interval(7, 1)
        self.assertEqual(bot._live_sessions[7]["interval"], 1)
        self.assertTrue(wake.is_set())
        bot._toggle_protocol(7)
        self.assertTrue(bot._live_sessions[7]["protocol_open"])
        bot._toggle_protocol(7)
        self.assertFalse(bot._live_sessions[7]["protocol_open"])

    def test_report_counts_outages(self):
        points = [{"in": 220, "load": 30}, {"in": 210, "load": 40}]
        events = [
            {"t": 100, "code": "on_battery", "message": "Сеть пропала"},
            {"t": 160, "code": "on_battery_clear", "message": "Сеть вернулась"},
        ]
        text = format_report(24, points, events, None)
        self.assertIn("Отчёт за сутки", text)
        self.assertNotIn("Точек:", text)
        self.assertIn("1 раз", text)
        self.assertIn("1 мин", text)

    def test_alert_keeps_the_event_title(self):
        text = format_alert(
            {"code": "on_battery", "message": "Сеть пропала, питание от батареи"},
            {"mains_ok": False, "input_v": 0, "output_v": 214},
        )
        self.assertTrue(text.startswith("Сеть пропала"))
        self.assertIn("Сеть: нет", text)

    def test_each_alert_has_its_own_picture(self):
        codes = [
            "on_battery", "on_battery_clear", "battery_low", "battery_low_clear",
            "fault", "fault_clear", "link_lost", "link", "pc_shutdown", "pc_off",
            "pc_reboot", "pc_sleep", "pc_hibernate", "battery_loose",
            "battery_loose_clear", "battery_swap", "startup", "report",
            "status", "screenshot", "voltage_low", "voltage_high",
            "voltage_crit_low", "voltage_crit_high", "battery_boost", "battery_buck",
        ]
        pictures = {}
        from io import BytesIO
        from PIL import Image
        for code in codes:
            png = notify_image(code)
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"), code)
            frame = Image.open(BytesIO(png))
            self.assertGreater(frame.size[0], frame.size[1], code)
            self.assertNotEqual(frame.size, (768, 768), code)
            pictures[code] = png
        self.assertNotEqual(pictures["on_battery"], pictures["on_battery_clear"])
        self.assertNotEqual(pictures["fault"], pictures["pc_sleep"])
        self.assertNotEqual(pictures["screenshot"], pictures["report"])
        self.assertNotEqual(pictures["voltage_low"], pictures["voltage_high"])
        self.assertNotEqual(pictures["voltage_crit_low"], pictures["voltage_low"])
        self.assertNotEqual(pictures["battery_boost"], pictures["battery_low"])
        self.assertNotEqual(pictures["battery_buck"], pictures["battery_boost"])
        self.assertNotEqual(pictures["link"], pictures["link_lost"])
        self.assertEqual(card_title("link"), "ИБП на связи")

    def test_card_title_stays_complete(self):
        long = "Напряжение батареи скачет — похоже, батареи отключены от ИБП"
        self.assertEqual(card_title("battery_loose", long), "Батареи отключены")
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (1280, 640)))
        _title_font, lines, _step = _fit_title(draw, long)
        joined = " ".join(lines)
        self.assertIn("отключены", joined)
        self.assertIn("ИБП", joined)
        self.assertFalse(joined.endswith("батарее"))

    def test_events_empty(self):
        self.assertEqual(format_events([]), "Журнал пуст.")

    def test_events_keep_readable_plain_text_for_illustrated_card(self):
        text = format_events([
            {"t": 1710000000, "message": "Сеть_пропала *авария*"}
        ])
        self.assertEqual(text.splitlines()[0], "Последние события:")
        self.assertIn("Сеть_пропала *авария*", text)
        self.assertNotIn(chr(96), text)
        self.assertNotIn("\\_", text)

    def test_token_is_stripped_from_public_settings(self):
        public = _public_settings({"telegram_token": "123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx", "telegram_chats": [{"id": 1, "name": "ivan"}]})
        self.assertEqual(public["telegram_token"], "")
        self.assertTrue(public["telegram_token_set"])
        self.assertEqual(public["telegram_chats"][0]["name"], "ivan")
        self.assertFalse(public["telegram_chats"][0]["guest"])

    def test_guest_chat_filters_events_and_buttons(self):
        guest = public_chat({
            "id": 7,
            "name": "сосед",
            "guest": True,
            "events": {"on_battery": True, "fault": False},
            "actions": {
                "status": True, "pc_off": False, "pc_reboot": False, "pc_sleep": False,
                "pc_hibernate": False, "cancel_shutdown": False, "screenshot": True,
            },
        })
        self.assertTrue(guest["guest"])
        self.assertTrue(chat_gets_event({}, guest, "on_battery"))
        self.assertFalse(chat_gets_event({}, guest, "fault"))
        self.assertTrue(chat_can(guest, "status"))
        self.assertTrue(chat_can(guest, "live_view"))
        self.assertFalse(chat_can(guest, "pc_off"))
        self.assertTrue(chat_can(guest, "screenshot"))
        self.assertFalse(chat_can(guest, "menu_pc"))
        rows = [cell for row in _menu_rows("root", guest) for cell in row]
        self.assertNotIn("ПК", rows)
        self.assertIn("ПО", rows)
        self.assertIn("Трансляция", [cell for row in _menu_rows("reports", guest) for cell in row])
        owner = {"id": 1, "name": "я", "guest": False}
        self.assertFalse(chat_gets_event({"telegram_events": {"fault": {"on": False}}}, owner, "fault"))
        self.assertTrue(chat_can(owner, "pc_off"))
        defaults = default_guest_events()
        self.assertTrue(defaults["on_battery"]["on"])
        self.assertTrue(defaults["on_battery"]["sound"])
        self.assertTrue(defaults["on_battery_clear"]["on"])
        self.assertFalse(defaults["on_battery_clear"]["sound"])
        self.assertFalse(defaults["fault"]["on"])
        self.assertTrue(chat_event_sound({}, {"guest": True, "events": {"on_battery": {"on": True, "sound": True}}}, "on_battery"))
        self.assertFalse(chat_event_sound({}, {"guest": True, "events": {"on_battery": {"on": True, "sound": False}}}, "on_battery"))
        admin = public_chat({"id": 2, "name": "админ", "admin": True})
        self.assertTrue(chat_is_admin(admin))
        self.assertIn("Чаты", [cell for row in _menu_rows("root", admin) for cell in row])
        own = {"id": 2, "admin": True, "events": {"on_battery": {"on": False, "sound": False}, "fault": {"on": True, "sound": False}}}
        self.assertTrue(chat_is_admin(own))
        self.assertTrue(chat_gets_event({}, own, "on_battery"))
        self.assertFalse(chat_gets_event({"telegram_events": {"fault": {"on": False}}}, own, "fault"))
        merged = merge_global_events(
            {"telegram_events": {"on_battery": {"on": True, "sound": True, "tray": False}}},
            {"on_battery": {"on": False, "sound": True}},
        )
        self.assertFalse(merged["on_battery"]["on"])
        self.assertFalse(merged["on_battery"]["sound"])
        self.assertFalse(merged["on_battery"]["tray"])
        self.assertEqual(extract_pair_code("482913"), "482913")
        self.assertEqual(extract_pair_code("/start 482913"), "482913")
        self.assertEqual(extract_pair_code("/start"), "")
        self.assertEqual(extract_pair_code("код 482913"), "")

    def test_admin_self_profile_edits_global_rules(self):
        class Host:
            def __init__(self):
                self.settings = {
                    "telegram_chats": [
                        {"id": 1, "name": "я", "admin": True},
                        {"id": 2, "name": "гость", "guest": True},
                    ],
                    "telegram_events": {"fault": {"on": False, "sound": False, "tray": True}},
                }
                self.chat_payloads = []
                self.global_patch = None

            def get_settings_raw(self):
                return self.settings

            def update_telegram_chat(self, payload):
                self.chat_payloads.append(payload)
                return {"ok": True}

            def update_global_telegram_events(self, patch, actor_id=None):
                self.global_patch = patch
                return {"ok": True}

        host = Host()
        bot = TelegramBridge(host)
        shown = []
        bot._admin_show = lambda chat_id, query, text, rows: shown.append((text, rows))
        bot._admin_open(1, 1)
        text, rows = shown[-1]
        labels = [cell["text"] for row in rows for cell in row]
        self.assertIn("общие", text.lower())
        self.assertIn("Уведомления", labels)
        self.assertNotIn("Гость", labels)
        self.assertNotIn("Кнопки", labels)
        bot._admin_callback(1, "a:r:1:guest", {})
        self.assertEqual(host.chat_payloads, [])
        bot._admin_callback(1, "a:e:1:fault:on", {})
        self.assertIsNotNone(host.global_patch)
        self.assertTrue(host.global_patch["fault"]["on"])
        self.assertEqual(host.chat_payloads, [])

    def test_history_decimate_keeps_recent_run(self):
        old = [{"t": float(i), "in": 220.0} for i in range(800)]
        gap = [{"t": 2000.0 + i, "in": 210.0} for i in range(80)]
        out = _decimate(old + gap, 200)
        self.assertLessEqual(len(out), 200)
        self.assertGreaterEqual(out[-1]["t"], 2070)
        self.assertTrue(any(point["t"] >= 2000 for point in out[-80:]))
        self.assertLess(out[-90]["t"], 2000)

    def test_close_notice_skips_when_bot_is_off(self):
        class Host:
            def get_settings_raw(self):
                return {"telegram_token": "", "telegram_chats": []}

        started = time.time()
        TelegramBridge(Host()).announce_stop(5)
        self.assertLess(time.time() - started, 0.4)

    def test_close_notice_does_not_wait_for_dead_network(self):
        class Host:
            def get_settings_raw(self):
                return {
                    "telegram_token": "123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                    "telegram_chats": [{"id": 1, "name": "ivan"}],
                }

        bot = TelegramBridge(Host())
        bot._token = "123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx"

        def hang(*_args, **_kwargs):
            time.sleep(30)

        started = time.time()
        with patch("ups.telegram._send_photo", hang), patch("ups.telegram._send", hang):
            bot.announce_stop(0.8)
        self.assertLess(time.time() - started, 2.0)
        self.assertEqual(card_title("shutdown"), "UPS закрыт")

    def test_poll_errors_do_not_kill_inbound_loop(self):
        self.assertFalse(poll_error_fatal("Telegram не ответил"))
        self.assertFalse(poll_error_fatal("Conflict: terminated by other getUpdates request"))
        self.assertTrue(poll_error_fatal("Unauthorized"))
        self.assertTrue(poll_error_fatal("can't use getUpdates if webhook is set"))
        host = type("Host", (), {
            "get_settings_raw": lambda self: {"telegram_token": "x", "telegram_chats": []},
        })()
        bot = TelegramBridge(host)
        bot._token = "x"
        bot._ok = True
        bot._stop.set()
        bot._on_poll_error(TelegramError("Telegram не ответил"))
        self.assertTrue(bot._ok)
        bot._on_poll_error(TelegramError("Unauthorized"))
        self.assertFalse(bot._ok)

    def test_api_wraps_broken_json(self):
        class FakeResp:
            def read(self):
                return b"NOT JSON"
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False

        with patch("urllib.request.urlopen", return_value=FakeResp()):
            with self.assertRaises(TelegramError):
                _api("token", "getMe")

    def test_simulation_catalog_is_safe_software_only(self):
        codes = [item["code"] for item in SIMULATIONS]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue({"on_battery", "pc_off", "app_restart", "voltage_crit_high", "battery_boost", "battery_buck"}.issubset(codes))
        for item in SIMULATIONS:
            self.assertIn(item["level"], {"ok", "info", "warn", "bad"})
            self.assertTrue(item["message"].startswith("Симуляция"))
            self.assertIn(item["group"], {"Сеть", "Батарея", "ИБП", "ПК", "Программа"})

    def test_tray_notify_only_when_hidden(self):
        tray = Tray(window=None)
        notes = []
        tray._icon = type("Icon", (), {"notify": lambda self, message, title: notes.append((title, message))})()
        tray.notify("ИБП на связи", "USB снова на месте")
        self.assertEqual(notes, [])
        self.assertEqual(tray.unread, 0)
        tray.hidden = True
        tray.notify("ИБП на связи", "USB снова на месте")
        self.assertEqual(notes, [("ИБП на связи", "USB снова на месте")])
        self.assertEqual(tray.unread, 1)
        self.assertIn("новых", tray._icon_title())
        badged = tray._icon_image()
        tray.unread = 0
        clean = tray._icon_image()
        self.assertNotEqual(list(badged.getdata()), list(clean.getdata()))

    def test_tray_double_click_opens_program(self):
        calls = []
        tray = Tray(window=type("Win", (), {
            "show": lambda self: calls.append("show"),
            "restore": lambda self: calls.append("restore"),
            "evaluate_js": lambda self, code: calls.append(code),
        })())
        tray._on_icon_click()
        tray._on_icon_click()
        self.assertIn("restore", calls)
        self.assertIn("show", calls)
        self.assertTrue(any("closeNoticeInbox" in str(item) for item in calls))
        self.assertIsNone(tray._click_timer)
        calls.clear()
        tray._on_icon_dblclick()
        self.assertIn("restore", calls)
        self.assertIn("show", calls)

    def test_tray_single_click_opens_inbox_near_tray(self):
        from unittest.mock import patch
        calls = []
        tray = Tray(window=type("Win", (), {
            "show": lambda self: calls.append("show"),
            "restore": lambda self: calls.append("restore"),
            "evaluate_js": lambda self, code: calls.append(code),
        })())
        tray.unread = 3
        tray.remember_notice("Сеть пропала", "Питание от батареи", "on_battery", "warn", 1710000000)
        with patch("ups.win_toast.toggle_inbox", lambda items, **kwargs: calls.append(("inbox", len(items)))):
            tray.open_notices()
        self.assertNotIn("show", calls)
        self.assertNotIn("restore", calls)
        self.assertEqual(tray.unread, 0)
        self.assertEqual(calls[0], ("inbox", 1))

    def test_tray_binds_double_click_on_notify_handler(self):
        calls = []
        tray = Tray(window=type("Win", (), {
            "show": lambda self: calls.append("show"),
            "restore": lambda self: calls.append("restore"),
            "evaluate_js": lambda self, code: calls.append(code),
        })())
        icon = type("Icon", (), {
            "_on_notify": lambda self, w, l: calls.append("orig"),
            "_message_handlers": {0x040B: None},
        })()
        tray._icon = icon
        tray._bind_icon_clicks()
        icon._message_handlers[0x040B](0, 0x0203)
        self.assertIn("show", calls)
        self.assertNotIn("orig", calls)

    def test_windows_toast_xml_uses_card_image(self):
        from ups.win_toast import _fit, inbox_size, notice_clock, notice_tone, show_card
        self.assertEqual(_fit((420, 210)), (420, 210))
        self.assertEqual(_fit((1260, 630)), (440, 220))
        self.assertEqual(inbox_size(0)[0], 380)
        self.assertLess(inbox_size(2)[1], inbox_size(8)[1])
        self.assertEqual(notice_tone({"code": "fault"}), "bad")
        self.assertEqual(notice_tone({"code": "on_battery_clear"}), "ok")
        self.assertEqual(notice_clock(0), "--:--:--")
        self.assertFalse(show_card(b""))

    def test_old_confirm_button_is_ignored(self):
        now = time.time()
        self.assertTrue(callback_fresh({"message": {"date": now - 10}}, now=now))
        self.assertFalse(callback_fresh({"message": {"date": now - 400}}, now=now))


if __name__ == "__main__":
    unittest.main()
