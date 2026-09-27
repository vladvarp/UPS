import time
import unittest
from datetime import datetime
from unittest.mock import patch

from ups.monitor import SIMULATIONS, _public_settings, _validate
from ups.notify_art import _fit_title, card_title, notify_image
from ups.telegram import (
    TOKEN_RE,
    TelegramBridge,
    callback_fresh,
    digest_due,
    event_pref,
    format_alert,
    format_events,
    format_report,
    parse_clock,
    status_lines,
)


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
        self.assertEqual(clean["telegram_events"]["on_battery"], {"on": True, "sound": False})
        self.assertNotIn("unknown", clean["telegram_events"])
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
            "fault", "fault_clear", "link_lost", "pc_shutdown", "pc_off",
            "pc_reboot", "pc_sleep", "pc_hibernate", "battery_loose",
            "battery_loose_clear", "battery_swap", "startup", "report",
            "status", "screenshot", "voltage_low", "voltage_high",
            "voltage_crit_low", "voltage_crit_high",
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

    def test_simulation_catalog_is_safe_software_only(self):
        codes = [item["code"] for item in SIMULATIONS]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue({"on_battery", "pc_off", "app_restart", "voltage_crit_high"}.issubset(codes))
        for item in SIMULATIONS:
            self.assertIn(item["level"], {"ok", "info", "warn", "bad"})
            self.assertTrue(item["message"].startswith("Симуляция"))
            self.assertIn(item["group"], {"Сеть", "Батарея", "ИБП", "ПК", "Программа"})

    def test_old_confirm_button_is_ignored(self):
        now = time.time()
        self.assertTrue(callback_fresh({"message": {"date": now - 10}}, now=now))
        self.assertFalse(callback_fresh({"message": {"date": now - 400}}, now=now))


if __name__ == "__main__":
    unittest.main()
