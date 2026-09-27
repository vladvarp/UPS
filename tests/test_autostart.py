import tempfile
import unittest
from pathlib import Path

from ups import autostart
from ups.monitor import _validate

ROOT = Path(__file__).resolve().parent.parent


class AutostartTests(unittest.TestCase):
    def test_validate_accepts_the_flag(self):
        clean, error = _validate({"autostart": True})
        self.assertIsNone(error)
        self.assertTrue(clean["autostart"])

    def test_enable_and_disable_write_a_shortcut(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            autostart.enable(folder)
            link = autostart.shortcut_path(folder)
            self.assertTrue(link.is_file())
            self.assertTrue(autostart.is_enabled(folder))
            autostart.disable(folder)
            self.assertFalse(link.exists())
            self.assertFalse(autostart.is_enabled(folder))

    def test_start_bat_asks_running_app_to_close(self):
        text = (ROOT / "start.bat").read_text(encoding="utf-8")
        admin = (ROOT / "start-admin.bat").read_text(encoding="utf-8")
        self.assertNotIn("taskkill", text.lower())
        self.assertNotIn("taskkill", admin.lower())
        self.assertIn("/api/quit", text)
        self.assertIn("/api/quit", admin)
        self.assertIn("close", text)


if __name__ == "__main__":
    unittest.main()
