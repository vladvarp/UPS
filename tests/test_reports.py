import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ups import reports
from ups.monitor import _validate
from ups.telegram import _MENUS


class ReportMathTests(unittest.TestCase):
    def test_default_folder_is_user_ups(self):
        path = reports.default_journal_dir()
        self.assertEqual(path.name, "UPS")
        self.assertEqual(path, Path.home() / "UPS")

    def test_day_buckets_keep_the_whole_day(self):
        start = datetime(2026, 9, 27, 0, 5).timestamp()
        noon = datetime(2026, 9, 27, 12, 0).timestamp()
        late = datetime(2026, 9, 27, 23, 50).timestamp()
        rows = [
            (start, 221.0, 220.0, 30, 26.6, 100, 50.0, 29.0, 0),
            (noon, 198.0, 220.0, 40, 26.4, 98, 50.0, 30.0, 0),
            (late, 251.0, 220.0, 20, 26.5, 99, 50.0, 28.0, 0),
        ]
        events = [{"t": noon, "level": "warn", "code": "voltage_low", "message": "Вход 198"}]
        day = reports.assemble_day("2026-09-27", rows, events)
        self.assertEqual(day["kind"], "day")
        self.assertEqual(len(day["points"]), 3)
        self.assertEqual(day["points"][0]["m"], 5)
        self.assertEqual(day["points"][-1]["m"], 23 * 60 + 50)
        self.assertEqual(day["stats"]["inMin"], 198.0)
        self.assertEqual(day["stats"]["inMax"], 251.0)
        self.assertEqual(len(day["events"]), 1)

    def test_empty_day_is_still_a_report(self):
        day = reports.assemble_day("2026-09-27", [], [])
        self.assertEqual(day["stats"]["samples"], 0)
        self.assertEqual(day["stats"]["outages"], 0)

    def test_html_is_self_contained(self):
        day = reports.assemble_day(
            "2026-09-27",
            [(datetime(2026, 9, 27, 10, 0).timestamp(), 220, 220, 35, 26.6, 100, 50, 29, 0)],
            [{"t": datetime(2026, 9, 27, 10, 1).timestamp(), "level": "bad", "code": "on_battery", "message": "Сеть пропала"}],
        )
        html = reports.render_html(day)
        self.assertIn("<style>", html)
        self.assertIn("const DATA = ", html)
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("href=\"http", html)
        self.assertIn("2026-09-27", html)
        self.assertIn("Сеть пропала", html)

    def test_json_and_template_live_in_the_user_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "UPS"
            day = reports.assemble_day("2026-09-27", [], [{"t": 1, "level": "info", "code": "on_battery", "message": "Сеть"}])
            reports.write_day(root, day)
            reports.write_summary(root, reports.build_summary(root, day))
            self.assertTrue((root / "days" / "2026-09-27.json").is_file())
            self.assertTrue((root / "template.html").is_file())
            self.assertTrue((root / "summary.json").is_file())
            self.assertTrue((root / "summary.html").is_file())
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["days"][0]["date"], "2026-09-27")
            self.assertIn("<style>", (root / "summary.html").read_text(encoding="utf-8"))

    def test_settings_accept_journal_path(self):
        clean, error = _validate({"journal_enabled": True, "journal_dir": str(Path.home() / "UPS")})
        self.assertIsNone(error)
        self.assertTrue(clean["journal_enabled"])
        self.assertTrue(clean["journal_dir"].endswith("UPS"))

    def test_telegram_menu_has_html_reports(self):
        buttons = [cell for row in _MENUS["reports"] for cell in row]
        self.assertIn("Отчёт дня", buttons)
        self.assertIn("Сводка", buttons)


if __name__ == "__main__":
    unittest.main()
