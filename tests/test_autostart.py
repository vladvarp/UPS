import tempfile
import unittest
from pathlib import Path

from ups import autostart
from ups.monitor import _validate


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


if __name__ == "__main__":
    unittest.main()
