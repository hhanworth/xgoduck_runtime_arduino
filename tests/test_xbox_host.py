"""Pure Linux Xbox mapping tests; no gamepad, evdev, hardware or network required."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from xbox_host import (PadState, EV_ABS, EV_KEY, EV_SYN, SYN_DROPPED,
                       ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ,
                       ABS_GAS, ABS_BRAKE,
                       BTN_SELECT, BTN_START, BTN_SOUTH, BTN_EAST, BTN_WEST,
                       BTN_NORTH, BTN_TL, ARM_CHORD_S, usable,
                       diagnostic, controller_layout, initial_state)

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

    def test_bluetooth_hid_layout_right_stick_and_gas_trigger(self):
        """Xbox Bluetooth HID exposes Z/RZ as sticks, BRAKE/GAS as triggers."""
        class InputDevice:
            def capabilities(self, absinfo=True):
                axes = [ABS_X, ABS_Y, ABS_Z, ABS_RZ, ABS_BRAKE, ABS_GAS]
                return {EV_ABS: axes, EV_KEY: [
                    BTN_SELECT, BTN_START, BTN_SOUTH, BTN_EAST,
                    BTN_WEST, BTN_NORTH]}
            def absinfo(self, code):
                lo, hi, value = ((0, 1023, 0) if code in (ABS_GAS, ABS_BRAKE)
                                 else (0, 65535, 32767))
                return SimpleNamespace(min=lo, max=hi, value=value)
            def active_keys(self):
                return []
        device = InputDevice()
        fake = SimpleNamespace(ecodes=SimpleNamespace(EV_ABS=EV_ABS, EV_KEY=EV_KEY))
        layout = controller_layout(device, fake)
        self.assertEqual(layout['name'], 'bluetooth-hid')
        self.assertEqual(layout['right_x'], ABS_Z)
        self.assertEqual(layout['right_y'], ABS_RZ)
        self.assertEqual(layout['right_trigger'], ABS_GAS)
        self.assertTrue(usable(device, fake))
        self.assertIn('layout=bluetooth-hid', diagnostic(device, fake))
        pad = initial_state(device, fake)
        self.assertTrue(pad.neutral())
        pad.feed(EV_ABS, ABS_Z, 0)
        self.assertAlmostEqual(pad.command()['twist'][2], 1.0)
        pad.feed(EV_ABS, ABS_RZ, 0)
        self.assertFalse(pad.neutral())
        pad.feed(EV_ABS, ABS_GAS, 1023)
        self.assertEqual(pad.command()['mouth'], 30)
        pad.feed(EV_ABS, ABS_GAS, 0)
        self.assertEqual(pad.command()['mouth'], 0)

    def test_missing_back_button_must_not_claim_controller(self):
        class MissingSafetyKey:
            def capabilities(self, absinfo=True):
                return {EV_ABS: [ABS_X, ABS_Y, ABS_Z, ABS_RZ,
                                 ABS_GAS, ABS_BRAKE],
                        EV_KEY: [BTN_START, BTN_SOUTH, BTN_EAST,
                                 BTN_WEST, BTN_NORTH]}
        fake = SimpleNamespace(ecodes=SimpleNamespace(EV_ABS=EV_ABS, EV_KEY=EV_KEY))
        self.assertFalse(usable(MissingSafetyKey(), fake))
        self.assertIn('BTN_SELECT', diagnostic(MissingSafetyKey(), fake))

    def test_ambiguous_trigger_only_axes_are_rejected(self):
        class BadPad:
            def capabilities(self, absinfo=True):
                return {EV_ABS: [ABS_X, ABS_Y, ABS_Z, ABS_RZ],
                        EV_KEY: [BTN_SELECT, BTN_START, BTN_SOUTH,
                                 BTN_EAST, BTN_WEST, BTN_NORTH]}
            def absinfo(self, code):
                return SimpleNamespace(min=0, max=65535 if code == ABS_X else 1023)
        fake = SimpleNamespace(ecodes=SimpleNamespace(EV_ABS=EV_ABS, EV_KEY=EV_KEY))
        self.assertFalse(usable(BadPad(), fake))

    def test_both_axis_groups_choose_full_range_right_stick(self):
        class DualPad:
            def capabilities(self, absinfo=True):
                return {EV_ABS: [ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_Z,
                                 ABS_RZ, ABS_GAS],
                        EV_KEY: [BTN_SELECT, BTN_START, BTN_SOUTH,
                                 BTN_EAST, BTN_WEST, BTN_NORTH]}
            def absinfo(self, code):
                span = 1023 if code in (ABS_RX, ABS_RY, ABS_GAS) else 65535
                return SimpleNamespace(min=0, max=span)
        fake = SimpleNamespace(ecodes=SimpleNamespace(EV_ABS=EV_ABS, EV_KEY=EV_KEY))
        self.assertEqual(controller_layout(DualPad(), fake)['name'], 'bluetooth-hid')

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
