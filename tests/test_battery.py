"""Protocol checks for the independent Uno Q battery telemetry packet.

Run: python3 -m unittest discover -s tests -v
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'python'))
from wire import BATTERY, decode_battery


class BatteryPacketTests(unittest.TestCase):
    def test_packet_format(self):
        self.assertEqual(BATTERY.size, 8)
        self.assertEqual(decode_battery(BATTERY.pack(b'DQB1', 8200, 100, 1)),
                         {'valid': True, 'voltage_v': 8.2, 'percent': 100})
        self.assertEqual(decode_battery(BATTERY.pack(b'DQB1', 6600, 0, 1)),
                         {'valid': True, 'voltage_v': 6.6, 'percent': 0})

    def test_bench_supply_is_valid_zero_percent(self):
        # A 5 V bench supply is a valid voltage; 0% does not mean a sensor failure.
        self.assertEqual(decode_battery(BATTERY.pack(b'DQB1', 5000, 0, 1)),
                         {'valid': True, 'voltage_v': 5.0, 'percent': 0})

    def test_missing_voltage_is_not_zero_percent(self):
        self.assertEqual(decode_battery(BATTERY.pack(b'DQB1', 0, 0, 0)),
                         {'valid': False, 'voltage_v': None, 'percent': None})

    def test_corrupt_frames_rejected(self):
        for frame in (
            b'',
            BATTERY.pack(b'BAD!', 7400, 50, 1),
            BATTERY.pack(b'DQB1', 7400, 50, 2),
            BATTERY.pack(b'DQB1', 7400, 101, 1),
            BATTERY.pack(b'DQB1', 3900, 0, 1),
            BATTERY.pack(b'DQB1', 14001, 100, 1),
            BATTERY.pack(b'DQB1', 7400, 50, 0),
        ):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                decode_battery(frame)


if __name__ == '__main__':
    unittest.main()
