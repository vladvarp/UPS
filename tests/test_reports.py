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
        self.assertIn('type="time"', html)
        self.assertIn("кВт", html)
        self.assertIn('id="filterMenu"', html)
        self.assertIn('hidden', html)

    def test_summary_html_has_date_filters(self):
        day = reports.assemble_day("2026-09-27", [], [{"t": 1, "level": "info", "code": "on_battery", "message": "Сеть"}])
        summary = reports.build_summary(Path(tempfile.gettempdir()) / "missing-ups-journal", day)
        html = reports.render_html(summary)
        self.assertIn('type="date"', html)
        self.assertIn("кВт", html)
        self.assertNotIn("src=\"http", html)
        self.assertIn("7 дней", html)
        self.assertLess(html.index('id="events"'), html.index('id="daysCard"'))
        self.assertIn("::-webkit-scrollbar", html)

    def test_one_kilowatt_for_one_hour_is_one_kwh(self):
        start = 1_700_000_000.0
        rows = [(start + index * 60, 50, 0) for index in range(61)]
        stats = reports.energy_from_samples(rows, 2.0)
        self.assertAlmostEqual(stats["energyKwh"], 1.0, places=2)
        self.assertEqual(stats["avgW"], 1000)
        self.assertEqual(stats["peakW"], 1000)
        self.assertEqual(stats["energyKwhBattery"], 0)

    def test_battery_energy_is_split_from_mains(self):
        start = 1_700_000_000.0
        rows = [(start, 50, 1), (start + 60, 50, 0), (start + 120, 50, 0)]
        stats = reports.energy_from_samples(rows, 2.0)
        self.assertAlmostEqual(stats["energyKwh"], 0.033, places=2)
        self.assertAlmostEqual(stats["energyKwhBattery"], 0.017, places=2)
        self.assertAlmostEqual(stats["energyKwhMains"], 0.017, places=2)

    def test_energy_does_not_invent_gaps(self):
        start = 1_700_000_000.0
        rows = [(start, 50, 0), (start + 3 * 3600, 50, 0)]
        stats = reports.energy_from_samples(rows, 2.0)
        self.assertAlmostEqual(stats["energyKwh"], 0.05, places=3)

    def test_day_stats_include_energy(self):
        start = datetime(2026, 9, 27, 10, 0).timestamp()
        rows = [
            (start + index * 60, 220, 220, 50, 26.6, 100, 50, 29, 0)
            for index in range(61)
        ]
        day = reports.assemble_day("2026-09-27", rows, [], kva=2.0)
        self.assertEqual(day["kva"], 2.0)
        self.assertAlmostEqual(day["stats"]["energyKwh"], 1.0, places=2)
        self.assertEqual(day["stats"]["samples"], 61)

    def test_energy_report_decimates_points(self):
        start = 1_700_000_000.0
        rows = [(start + index, 20, 0) for index in range(800)]
        payload = reports.energy_report(rows, 0.7, start, start + 800)
        self.assertTrue(payload["ok"])
        self.assertLessEqual(len(payload["points"]), 480)
        self.assertGreater(payload["energyKwh"], 0)

    def test_energy_bucket_step_grows_with_span(self):
        start = 1_700_000_000.0
        self.assertEqual(reports.energy_bucket_step(start, start + 3600), 0)
        self.assertEqual(reports.energy_bucket_step(start, start + 10 * 3600), 15)
        self.assertEqual(reports.energy_bucket_step(start, start + 10 * 86400), 60)

    def test_bucketed_energy_stays_close_to_raw(self):
        start = 1_700_000_000.0
        rows = [(start + index * 2, 50, 0) for index in range(1801)]
        full = reports.energy_report(rows, 2.0, start, start + 3600)
        buckets = [(start + index * 60, 50, 0, 50) for index in range(61)]
        coarse = reports.energy_report(buckets, 2.0, start, start + 3600)
        self.assertAlmostEqual(full["energyKwh"], 1.0, places=2)
        self.assertAlmostEqual(coarse["energyKwh"], full["energyKwh"], delta=0.03)
        self.assertEqual(coarse["peakW"], 1000)

    def test_energy_from_db_uses_sql_buckets(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE readings (ts REAL, load_pct REAL, on_battery INTEGER, source TEXT)"
        )
        start = 1_700_000_000.0
        end = start + 10 * 86400
        rows = []
        ts = start
        while ts < end:
            rows.append((ts, 50, 0, "live"))
            ts += 120
        conn.executemany("INSERT INTO readings VALUES (?,?,?,?)", rows)
        conn.commit()
        payload = reports.energy_from_db(conn, 2.0, start, end)
        self.assertTrue(payload["ok"])
        self.assertGreater(payload["energyKwh"], 200)
        self.assertLessEqual(len(payload["points"]), 480)
        self.assertGreaterEqual(len(payload["days"]), 9)
        conn.close()

    def test_old_day_json_gets_energy_from_points(self):
        points = [{"m": index, "load": 50, "on": 0} for index in range(61)]
        day = {"date": "2026-09-27", "points": points, "stats": {"samples": 61, "loadAvg": 50}}
        out = reports.ensure_energy(day, 2.0)
        self.assertAlmostEqual(out["stats"]["energyKwh"], 1.0, places=2)

    def test_compact_day_without_points_does_not_fake_zero_kwh(self):
        day = {"date": "2026-09-27", "stats": {"samples": 1400, "loadAvg": 40}}
        out = reports.ensure_energy(day, 0.7)
        self.assertIsNone((out.get("stats") or {}).get("energyKwh"))

    def test_ensure_energy_from_raw_rows(self):
        start = 1_700_000_000.0
        rows = [(start + index * 60, 50, 0) for index in range(61)]
        day = {"date": "2026-09-27", "stats": {"loadAvg": 50}}
        out = reports.ensure_energy(day, 2.0, rows)
        self.assertAlmostEqual(out["stats"]["energyKwh"], 1.0, places=2)

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
