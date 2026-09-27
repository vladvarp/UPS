import unittest
from unittest.mock import patch

from ups.monitor import _validate
from ups.transport import TransportError, probe_serial
from ups.protocol import (
    battery_percent,
    battery_voltage_chaotic,
    battery_voltage_stepped,
    load_watts,
    mains_voltage_active,
    mode_label,
    parse_q1,
    parse_ratings,
    plausible,
    runtime_minutes,
    voltage_note,
    voltage_state,
)


class ParseTests(unittest.TestCase):
    def test_q1_from_the_live_ups_shape(self):
        status = parse_q1("(216.0 189.0 215.0 045 49.6 26.6 29.0 00001000\r")
        self.assertEqual(status.input_v, 216.0)
        self.assertEqual(status.output_v, 215.0)
        self.assertEqual(status.load_pct, 45)
        self.assertEqual(status.frequency, 49.6)
        self.assertEqual(status.battery_v, 26.6)
        self.assertEqual(status.temperature, 29.0)
        self.assertFalse(status.on_battery)
        self.assertTrue(status.offline_type)
        self.assertEqual(mode_label(status), "Нормальный режим")

    def test_q1_ignores_a_dirty_prefix(self):
        status = parse_q1("\x00junk (208.4 140.0 208.4 034 59.9 2.05 35.0 00110000")
        self.assertEqual(status.input_v, 208.4)
        self.assertTrue(status.avr)
        self.assertTrue(status.fault)
        self.assertIsNotNone(status.temperature)

    def test_missing_temperature(self):
        status = parse_q1("(220.0 180.0 220.0 010 50.0 27.2 --.- 10000000")
        self.assertIsNone(status.temperature)
        self.assertTrue(status.on_battery)

    def test_ratings(self):
        ratings = parse_ratings("#220.0 003 24.00 50.0")
        self.assertEqual(ratings["va"], 660)
        self.assertEqual(ratings["battery_v"], 24.0)


class BatteryTests(unittest.TestCase):
    def test_float_voltage_is_full(self):
        # 26.6 V and 26.4 V on 2 batteries: UPSmart shows about 100% on mains.
        for voltage in (26.6, 26.4):
            percent = battery_percent(voltage, 2, on_battery=False, full_v=13.5, eod_v=10.5)
            self.assertEqual(percent, 100, voltage)

    def test_zero_frame_is_rejected(self):
        status = parse_q1("(000.0 000.0 000.0 000 00.0 00.0 00.0 00000000")
        self.assertFalse(plausible(status))

    def test_empty_pack_is_zero(self):
        percent = battery_percent(21.0, 2, on_battery=True, full_v=13.5, eod_v=10.5)
        self.assertEqual(percent, 0)

    def test_runtime_at_the_shown_load(self):
        minutes = runtime_minutes(45, amp_hours=12, kva=0.7, health_pct=100)
        self.assertEqual(minutes, 26)

    def test_larger_battery_lasts_longer(self):
        small = runtime_minutes(45, 12, 0.7, 100)
        large = runtime_minutes(45, 24, 0.7, 100)
        self.assertGreater(large, small)

    def test_load_watts_from_kva(self):
        self.assertEqual(load_watts(35, 0.7), 245)

    def test_stable_pack_is_not_chaotic(self):
        values = [26.6, 26.7, 26.6, 26.5, 26.6, 26.7, 26.6, 26.6, 26.5]
        self.assertFalse(battery_voltage_chaotic(values))

    def test_jumping_pack_is_chaotic(self):
        values = [26.4, 22.1, 27.8, 19.4, 28.1, 18.0, 27.0, 20.2, 26.9]
        self.assertTrue(battery_voltage_chaotic(values))
        self.assertIsNone(battery_voltage_stepped(values))

    def test_swapped_pack_is_a_step_not_chaos(self):
        values = [26.6, 26.7, 26.6, 26.5, 26.6, 24.1, 24.0, 24.1, 24.0, 24.1]
        self.assertFalse(battery_voltage_chaotic(values))
        step = battery_voltage_stepped(values)
        self.assertIsNotNone(step)
        old, new = step
        self.assertAlmostEqual(old, 26.6, delta=0.2)
        self.assertAlmostEqual(new, 24.1, delta=0.2)

    def test_slow_discharge_is_not_a_swap(self):
        values = [26.6, 26.5, 26.4, 26.3, 26.2, 26.1, 26.0, 25.9, 25.8, 25.7]
        self.assertFalse(battery_voltage_chaotic(values))
        self.assertIsNone(battery_voltage_stepped(values))

    def test_single_spike_is_not_a_swap(self):
        values = [26.6, 26.6, 26.6, 26.5, 26.6, 22.0, 26.6, 26.5, 26.6, 26.6]
        self.assertIsNone(battery_voltage_stepped(values))


class VoltageAlertTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(voltage_state(220, False), "ok")
        self.assertEqual(voltage_state(199.9, False), "low")
        self.assertEqual(voltage_state(240.1, False), "high")
        self.assertEqual(voltage_state(179.9, False), "crit_low")
        self.assertEqual(voltage_state(250.1, False), "crit_high")
        self.assertEqual(voltage_state(0, True), "idle")

    def test_low_uses_hysteresis(self):
        self.assertTrue(mains_voltage_active("voltage_low", 199, False, False))
        self.assertFalse(mains_voltage_active("voltage_low", 202, False, False))
        self.assertTrue(mains_voltage_active("voltage_low", 202, False, True))
        self.assertFalse(mains_voltage_active("voltage_low", 206, False, True))

    def test_crit_high_uses_hysteresis(self):
        self.assertTrue(mains_voltage_active("voltage_crit_high", 251, False, False))
        self.assertFalse(mains_voltage_active("voltage_crit_high", 248, False, False))
        self.assertTrue(mains_voltage_active("voltage_crit_high", 248, False, True))
        self.assertFalse(mains_voltage_active("voltage_crit_high", 244, False, True))

    def test_battery_mode_is_not_a_voltage_alarm(self):
        self.assertFalse(mains_voltage_active("voltage_low", 0, True, True))
        self.assertFalse(mains_voltage_active("voltage_crit_low", 12, True, False))

    def test_custom_thresholds(self):
        limits = {"volt_low": 210, "volt_high": 230, "volt_crit_low": 190, "volt_crit_high": 245}
        self.assertEqual(voltage_state(205, False, limits), "low")
        self.assertEqual(voltage_state(235, False, limits), "high")
        self.assertEqual(voltage_note("low", limits), "Вход ниже 210 В")
        self.assertTrue(mains_voltage_active("voltage_high", 231, False, False, limits))
        self.assertFalse(mains_voltage_active("voltage_high", 229, False, False, limits))

    def test_voltage_order_is_checked(self):
        clean, error = _validate({"volt_low": 240, "volt_high": 200, "volt_crit_low": 180, "volt_crit_high": 250})
        self.assertIn("Пороги", error or "")
        self.assertEqual(clean, {})

    def test_ups_kva_accepts_fraction_and_va(self):
        clean, error = _validate({"ups_kva": "0,72"})
        self.assertIsNone(error)
        self.assertEqual(clean["ups_kva"], 0.72)
        clean, error = _validate({"ups_kva": 720})
        self.assertIsNone(error)
        self.assertEqual(clean["ups_kva"], 0.72)


class PortScanTests(unittest.TestCase):
    def test_scan_ports_lists_hid_and_com(self):
        from ups.monitor import Monitor
        from ups.transport import DeviceBusy, FoundDevice

        device = FoundDevice("\\\\?\\hid#vid_0001&pid_0000", 1, 0, "", "")
        with patch("ups.monitor.scan_hid", return_value=[device]), patch(
            "ups.monitor.list_serial_ports", return_value=[]
        ), patch("ups.monitor.open_mega", side_effect=DeviceBusy("x")):
            monitor = Monitor.__new__(Monitor)
            monitor._transport = None
            monitor._lock = __import__("threading").Lock()
            monitor._ports = []
            monitor._ports_at = 0
            result = Monitor.scan_ports(monitor)
        self.assertTrue(result["ok"])
        self.assertEqual(result["hid"][0]["id"], "0001:0000")
        self.assertEqual(result["hid"][0]["status"], "занят другой программой")
        self.assertEqual(result["com"], [])


class SerialProbeTests(unittest.TestCase):
    def test_falls_back_when_saved_port_is_gone(self):
        ports = [
            {"device": "COM3", "description": "USB-SERIAL CH340", "hwid": "USB VID:PID=1A86:7523"},
            {"device": "COM5", "description": "USB-SERIAL CH340", "hwid": "USB VID:PID=1A86:7523"},
        ]

        def fake_open(port):
            if port == "COM3":
                raise TransportError("gone")
            return type("T", (), {"port": port, "label": port})()

        with patch("ups.transport.list_serial_ports", return_value=ports), patch(
            "ups.transport.open_serial", side_effect=fake_open
        ):
            found = probe_serial("COM3")
        self.assertEqual(found.port, "COM5")


if __name__ == "__main__":
    unittest.main()
