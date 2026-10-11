"""Pure Linux Xbox mapping tests; no gamepad, evdev, hardware or network required."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from xbox_host import (PadState, EV_ABS, EV_KEY, EV_SYN, SYN_DROPPED,
                       ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_RZ,
                       BTN_SELECT, BTN_START, BTN_SOUTH, BTN_EAST, BTN_WEST,
                       BTN_NORTH, BTN_TL, ARM_CHORD_S, usable)

RANGES = {code: (0, 255, 127.5) for code in (ABS_X, ABS_Y, ABS_RX, ABS_RY)}
RANGES[ABS_RZ] = (0, 1023, 0)


class XboxMappingTests(unittest.TestCase):
    def test_linux_evdev_axis_capabilities_with_absinfo(self):
        # Actual python-evdev defaults to (axis_code, AbsInfo) tuples.
        # The gamepad probe must explicitly request integer codes.
        class InputDevice:
            def capabilities(self, absinfo=True):
                axes = list((ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_RZ))
                return {
                    EV_ABS: [(axis, object()) for axis in axes] if absinfo else axes,
                    EV_KEY: [BTN_SELECT, BTN_START, BTN_SOUTH, BTN_EAST,
                             BTN_WEST, BTN_NORTH],
                }
        ecodes = SimpleNamespace(EV_ABS=EV_ABS, EV_KEY=EV_KEY)
        self.assertTrue(usable(InputDevice(), SimpleNamespace(ecodes=ecodes)))

    def test_neutral_deadzone_and_scaled_directions(self):
        pad = PadState(RANGES)
        self.assertTrue(pad.neutral())
        pad.feed(EV_ABS, ABS_Y, 0)       # forward
        pad.feed(EV_ABS, ABS_X, 255)     # strafe right (negative vy)
        pad.feed(EV_ABS, ABS_RX, 0)      # turn left (positive yaw)
        twist = pad.command()['twist']
        self.assertAlmostEqual(twist[0], 0.4)
        self.assertAlmostEqual(twist[1], -0.3)
        self.assertAlmostEqual(twist[2], 1.0)
        self.assertFalse(pad.neutral())

    def test_lb_head_axes_cancel_twist(self):
        pad = PadState(RANGES)
        pad.feed(EV_ABS, ABS_X, 255)
        pad.feed(EV_ABS, ABS_Y, 0)
        pad.feed(EV_KEY, BTN_TL, 1)
        self.assertEqual(pad.command()['twist'], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(pad.command()['head'][0], 1.0)
        self.assertAlmostEqual(pad.command()['head'][1], 1.0)

    def test_trigger_limits(self):
        pad = PadState(RANGES)
        self.assertEqual(pad.command()['mouth'], 0)
        pad.feed(EV_ABS, ABS_RZ, 1023)
        self.assertEqual(pad.command()['mouth'], 30)

    def test_chord_needs_release_neutral_and_one_second(self):
        pad = PadState(RANGES, [BTN_SELECT, BTN_START])
        self.assertFalse(pad.arm_ready(1.0))  # held before service starts
        pad.feed(EV_KEY, BTN_SELECT, 0)
        self.assertFalse(pad.arm_ready(1.1))
        pad.feed(EV_KEY, BTN_SELECT, 1)
        self.assertFalse(pad.arm_ready(1.2))
        self.assertFalse(pad.arm_ready(1.2 + ARM_CHORD_S - 0.01))
        self.assertTrue(pad.arm_ready(1.2 + ARM_CHORD_S))
        pad.set_owned(True)
        self.assertFalse(pad.arm_ready(5.0))  # holding the chord cannot rearm
        pad.set_owned(False)
        self.assertFalse(pad.arm_ready(7.0))
        pad.feed(EV_KEY, BTN_START, 0)
        self.assertFalse(pad.arm_ready(7.1))
        pad.feed(EV_KEY, BTN_START, 1)
        self.assertFalse(pad.arm_ready(7.2))
        self.assertTrue(pad.arm_ready(8.21))

    def test_chord_rejects_non_neutral_sticks(self):
        pad = PadState(RANGES)
        pad.arm_ready(0.0)  # observe release
        pad.feed(EV_KEY, BTN_SELECT, 1)
        pad.feed(EV_KEY, BTN_START, 1)
        pad.feed(EV_ABS, ABS_X, 255)
        self.assertFalse(pad.arm_ready(0.5))
        self.assertFalse(pad.arm_ready(2.0))
        pad.feed(EV_ABS, ABS_X, 127.5)
        self.assertFalse(pad.arm_ready(3.0))
        self.assertTrue(pad.arm_ready(4.01))

    def test_buttons_are_edges_and_stop_wins(self):
        pad = PadState(RANGES)
        pad.set_owned(True)
        for button in (BTN_START, BTN_SOUTH, BTN_WEST, BTN_NORTH, BTN_EAST):
            pad.feed(EV_KEY, button, 1)
        self.assertEqual(pad.next_action(), 'off')
        self.assertIsNone(pad.next_action())
        pad.feed(EV_KEY, BTN_SOUTH, 1)  # repeated pressed event
        self.assertIsNone(pad.next_action())
        pad.feed(EV_KEY, BTN_SOUTH, 0)
        pad.feed(EV_KEY, BTN_SOUTH, 1)
        self.assertEqual(pad.next_action(), 'hold')

    def test_sync_loss_is_fatal(self):
        pad = PadState(RANGES)
        with self.assertRaises(OSError):
            pad.feed(EV_SYN, SYN_DROPPED, 0)


if __name__ == '__main__':
    unittest.main()
