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
    format_events,
    format_report,
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
            "voltage_crit_low", "voltage_crit_high", "battery_boost",
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
        self.assertFalse(chat_can(guest, "pc_off"))
        self.assertTrue(chat_can(guest, "screenshot"))
        self.assertFalse(chat_can(guest, "menu_pc"))
        rows = [cell for row in _menu_rows("root", guest) for cell in row]
        self.assertNotIn("ПК", rows)
        self.assertIn("ПО", rows)
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
        self.assertTrue({"on_battery", "pc_off", "app_restart", "voltage_crit_high", "battery_boost"}.issubset(codes))
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
