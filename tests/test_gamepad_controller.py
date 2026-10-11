"""Controller gamepad lease/watchdog tests; no ONNX models or real motors needed."""
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

# Controller imports Policy at module scope. Replace only the ONNX-dependent class.
policy_module = types.ModuleType('policy')
class DummyPolicy:
    def reset_action_filter(self): pass
    def warm(self, _count): pass
    def set_action_alphas(self, **_kw): pass
policy_module.Policy = DummyPolicy
with patch.dict(sys.modules, {'policy': policy_module}):
    from controller import Controller, GAMEPAD_TIMEOUT_S
from wire import COMMAND, DISARM


class DummyBridge:
    def __init__(self):
        self.calls = []
    def notify(self, name, payload):
        self.calls.append((name, payload))


class DummyRecovery:
    phase = 'walk'
    def reset(self): pass


def build_controller():
    # Avoid loading actual ONNX sessions; exercise unmodified Controller methods.
    c = Controller.__new__(Controller)
    c.bridge = DummyBridge()
    c.lock = threading.RLock()
    c.policy_lock = threading.Lock()
    c.policy = c.getup_policy = c.pick_policy = c.roulade_policy = DummyPolicy()
    c.recovery = DummyRecovery()
    c.feedback = None
    c.filtered = None
    c.rx_time = 0.0
    c.mode = 'shadow'
    c.generation = 0
    c.input_owner = 'web'
    c.gamepad_session = None
    c.gamepad_seen = 0.0
    c.web_time = 0.0
    c.twist, c.head, c.mouth = [0.0]*3, [0.0]*4, 0.0
    c.arm_pending = False
    c.seq = c.sent = c.inferred = 0
    c.last_error = ''
    c.policy_needs_reset = True
    c.pick_active = False
    c.pick_phi = 0.0
    c.roulade_until = 0.0
    c._roulade_alpha = None
    return c


class GamepadControllerTests(unittest.TestCase):
    def test_claim_disarms_and_web_cannot_override(self):
        c = build_controller()
        session = c.gamepad_claim()['session']
        self.assertEqual(c.input_owner, 'gamepad')
        self.assertEqual(c.mode, 'off')
        self.assertEqual(COMMAND.unpack(c.bridge.calls[-1][1])[4], DISARM)
        with self.assertRaises(ValueError):
            c.command(twist=[0, 0, 0])
        with self.assertRaises(ValueError):
            c.set_mode('policy')
        with self.assertRaises(ValueError):
            c.gamepad_claim()
        c.set_mode('off')  # Emergency stop from the web always wins.
        self.assertIsNone(c.gamepad_session)
        self.assertEqual(c.input_owner, 'web')
        with self.assertRaises(ValueError):
            c.gamepad_input(session, [0]*3, [0]*4, 0)

    def test_valid_gamepad_command_and_explicit_walk(self):
        c = build_controller()
        c.feedback = SimpleNamespace(imu_ok=True, mask=0, enabled=False, sample_us=55)
        c.rx_time = time.monotonic()
        session = c.gamepad_claim()['session']
        out = c.gamepad_input(session, [.2, -.1, .4], [0]*4, 13,
                              action='policy')
        self.assertTrue(out['connected'])
        self.assertEqual(c.mode, 'policy')
        self.assertTrue(c.arm_pending)
        self.assertEqual(c.twist, [.2, -.1, .4])
        self.assertEqual(c.mouth, 13)
        with self.assertRaises(ValueError):
            c.gamepad_input(session, [10, 0, 0], [0]*4, 0)
        self.assertEqual(c.twist, [.2, -.1, .4])

    def test_invalid_motion_cannot_arm_on_action(self):
        c = build_controller()
        c.feedback = SimpleNamespace(imu_ok=True, mask=0, enabled=False, sample_us=55)
        c.rx_time = time.monotonic()
        session = c.gamepad_claim()['session']
        with self.assertRaises(ValueError):
            c.gamepad_input(session, [5, 0, 0], [0]*4, 0, action='policy')
        self.assertEqual(c.mode, 'off')
        self.assertFalse(c.arm_pending)

    def test_sensor_loss_revokes_and_requires_new_chord(self):
        c = build_controller()
        c.feedback = SimpleNamespace(imu_ok=False, mask=0, enabled=False, sample_us=55)
        c.rx_time = time.monotonic()
        session = c.gamepad_claim()['session']
        # Simulate a previously armed walk when the IMU disappears.
        c.mode = 'policy'
        c.step()
        self.assertEqual(c.mode, 'off')
        self.assertIsNone(c.gamepad_session)
        with self.assertRaises(ValueError):
            c.gamepad_input(session, [0]*3, [0]*4, 0)

    def test_session_mismatch_and_release(self):
        c = build_controller()
        session = c.gamepad_claim()['session']
        with self.assertRaises(ValueError):
            c.gamepad_input('fake-session', [0]*3, [0]*4, 0)
        self.assertEqual(c.input_owner, 'gamepad')
        self.assertFalse(c.gamepad_release('fake-session')['ok'])
        self.assertTrue(c.gamepad_release(session)['ok'])
        self.assertEqual(c.mode, 'off')
        self.assertFalse(c.gamepad_release(session)['ok'])

    def test_gamepad_ttl_revokes_and_requires_new_chord(self):
        c = build_controller()
        session = c.gamepad_claim()['session']
        c.gamepad_seen -= GAMEPAD_TIMEOUT_S + 0.1
        with self.assertRaises(ValueError):
            c.gamepad_input(session, [0]*3, [0]*4, 0)
        self.assertIsNone(c.gamepad_session)
        self.assertEqual(c.mode, 'off')
        self.assertIn('expired', c.last_error)

    def test_policy_loop_revokes_on_missed_heartbeat(self):
        c = build_controller()
        c.gamepad_claim()
        c.gamepad_seen -= GAMEPAD_TIMEOUT_S + 0.1
        c.step()
        self.assertIsNone(c.gamepad_session)
        self.assertEqual(c.mode, 'off')
        self.assertIn('heartbeat lost', c.last_error)

    def test_b_button_emergency_stop_does_not_require_good_axes(self):
        c = build_controller()
        session = c.gamepad_claim()['session']
        result = c.gamepad_input(session, 'not an axis', None, None, action='off')
        self.assertFalse(result['connected'])
        self.assertEqual(c.mode, 'off')
        self.assertIsNone(c.gamepad_session)


if __name__ == '__main__':
    unittest.main()
