#!/usr/bin/env python3
"""Read an Xbox pad on the UNO Q Linux *host* and feed the Arduino App container.

Run on the Debian host, NOT inside Arduino App Lab's Docker container.
Only the optional python-evdev library is needed on the host.
The 50 Hz ONNX policy and MCU command transport remain in python/controller.py.
"""
import argparse
import json
import os
import select
import signal
import sys
import time
from urllib import error, request

# Stable Linux input-event-codes.h values. Importing this module needs no evdev.
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_DROPPED = 3
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = range(6)
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 304, 305, 307, 308
BTN_TL, BTN_TR2, BTN_SELECT, BTN_START = 310, 313, 314, 315
REQUIRED_ABS = {ABS_X, ABS_Y, ABS_RX, ABS_RY}
REQUIRED_KEYS = {BTN_SOUTH, BTN_EAST, BTN_WEST, BTN_NORTH, BTN_START}
BUTTON_ACTIONS = {BTN_EAST: 'off', BTN_SOUTH: 'hold', BTN_START: 'policy',
                  BTN_WEST: 'pick', BTN_NORTH: 'roulade'}
PERIOD_S = 0.05
ARM_CHORD_S = 1.0
DEADZONE = 0.12


def deadzone(value):
    value = max(-1.0, min(1.0, float(value)))
    if abs(value) <= DEADZONE:
        return 0.0
    return (1 if value > 0 else -1) * (abs(value) - DEADZONE) / (1 - DEADZONE)


class PadState:
    """Pure event mapper, suitable for tests without hardware or root privileges."""

    def __init__(self, ranges, initial_keys=()):
        # ranges: {abs_code: (min, max, initial_value)}
        self.ranges = dict(ranges)
        self.axes = {code: initial for code, (_lo, _hi, initial) in ranges.items()}
        self.keys = set(initial_keys)
        self.queued = set()
        self.owned = False
        self.chord_since = None
        self.chord_blocked = True  # Must observe *release* before arming.

    def feed(self, event_type, code, value):
        if event_type == EV_SYN and code == SYN_DROPPED:
            raise OSError('input SYN_DROPPED; forcing disarm and re-scan')
        if event_type == EV_ABS and code in self.ranges:
            self.axes[code] = value
        elif event_type == EV_KEY and value in (0, 1):
            before = code in self.keys
            if value:
                self.keys.add(code)
                if self.owned and not before and code in BUTTON_ACTIONS:
                    self.queued.add(BUTTON_ACTIONS[code])
            else:
                self.keys.discard(code)

    def stick(self, code):
        if code not in self.ranges:
            return 0.0
        low, high, _ = self.ranges[code]
        if high <= low:
            return 0.0
        center = (high + low) / 2
        radius = (high - low) / 2
        return deadzone((self.axes[code] - center) / radius)

    def trigger(self, code):
        if code in self.ranges:
            low, high, _ = self.ranges[code]
            if high > low:
                return max(0.0, min(1.0, (self.axes[code] - low) / (high - low)))
        return 1.0 if BTN_TR2 in self.keys else 0.0

    def neutral(self):
        return all(self.stick(code) == 0 for code in REQUIRED_ABS)

    def arm_ready(self, now):
        both = BTN_SELECT in self.keys and BTN_START in self.keys
        if not both:
            self.chord_blocked = False
            self.chord_since = None
            return False
        if self.chord_blocked or not self.neutral():
            self.chord_since = None
            return False
        if self.chord_since is None:
            self.chord_since = now
        return now - self.chord_since >= ARM_CHORD_S

    def set_owned(self, owned):
        self.owned = owned
        self.queued.clear()
        self.chord_since = None
        self.chord_blocked = True

    def next_action(self):
        for action in ('off', 'hold', 'policy', 'pick', 'roulade'):
            if action in self.queued:
                self.queued.clear()
                return action
        return None

    def command(self):
        lx, ly = self.stick(ABS_X), self.stick(ABS_Y)
        rx, ry = self.stick(ABS_RX), self.stick(ABS_RY)
        shift = BTN_TL in self.keys
        return {
            'twist': [0.0, 0.0, 0.0] if shift else [-ly * 0.4, -lx * 0.3, -rx],
            'head': [lx, -ly, rx, -ry] if shift else [0.0] * 4,
            'mouth': round(self.trigger(ABS_RZ) * 30),
        }


class API:
    def __init__(self, base):
        self.base = base.rstrip('/')

    def post(self, path, payload):
        data = json.dumps(payload).encode('utf-8')
        req = request.Request(self.base + '/api/gamepad/' + path, data=data,
                              headers={'Content-Type': 'application/json'}, method='POST')
        with request.urlopen(req, timeout=0.2) as reply:
            return json.load(reply)


def usable(device, evdev):
    # python-evdev's default absinfo=True returns (code, AbsInfo) pairs
    # for EV_ABS, which cannot be compared to plain integer axis codes.
    caps = device.capabilities(absinfo=False)
    axes = set(caps.get(evdev.ecodes.EV_ABS, []))
    keys = set(caps.get(evdev.ecodes.EV_KEY, []))
    return REQUIRED_ABS <= axes and REQUIRED_KEYS <= keys


def find_pad(evdev, selected=None):
    paths = [selected] if selected else evdev.list_devices()
    for path in paths:
        try:
            pad = evdev.InputDevice(path)
        except (OSError, PermissionError) as exc:
            if selected:
                print(f'Cannot open {path}: {exc}', file=sys.stderr)
            continue
        if usable(pad, evdev) and (selected or
            pad.info.vendor == 0x045e or any(word in pad.name.lower() for word in
            ('xbox', 'xinput', 'x-input', 'microsoft', '8bitdo'))):
            return pad
        pad.close()
    return None


def initial_state(pad, evdev):
    ranges = {}
    for code in REQUIRED_ABS | {ABS_RZ, ABS_Z}:
        try:
            info = pad.absinfo(code)
            if info is not None:
                ranges[code] = (info.min, info.max, info.value)
        except (OSError, ValueError):
            continue
    return PadState(ranges, pad.active_keys())


def serve_device(pad, evdev, api, running):
    state = initial_state(pad, evdev)
    session = None
    last_send = 0.0
    print(f'Xbox input ready: {pad.name} ({pad.path}). '
          'Release both sticks; hold Back + Start for 1 second to claim control.')
    try:
        while running():
            # On Bluetooth/USB removal some kernels keep the old fd open.
            # Do not keep sending stale stick values just because select()
            # returns no more events. A new device must re-arm explicitly.
            if not os.path.exists(pad.path):
                raise OSError('gamepad input device disappeared')
            ready, _, _ = select.select([pad.fd], [], [], 0.02)
            if ready:
                for event in pad.read():
                    state.feed(event.type, event.code, event.value)
            now = time.monotonic()
            if session is None:
                if state.arm_ready(now):
                    try:
                        result = api.post('claim', {})
                    except (OSError, ValueError, error.HTTPError) as exc:
                        print(f'Gamepad claim failed: {exc}', file=sys.stderr)
                        state.set_owned(False)  # Require release and new chord.
                    else:
                        session = result['session']
                        state.set_owned(True)
                        last_send = 0.0
                        print('Gamepad claimed. Torque OFF. Press A for default pose '
                              'or Start to walk; B always disarms.')
                continue
            # Stop button bypasses the normal periodic command interval.
            urgent = 'off' in state.queued
            if not urgent and now - last_send < PERIOD_S:
                continue
            last_send = now
            action = state.next_action()
            payload = {'session': session, **state.command(), 'action': action}
            try:
                response = api.post('input', payload)
            except error.HTTPError as exc:
                # Pick/roulade may be rejected while not upright; keep heartbeat
                # next tick. All other rejected operations revoke local ownership.
                if exc.code == 409 and action in ('pick', 'roulade'):
                    print(f'{action} denied (not upright or conflicting mode)', file=sys.stderr)
                    continue
                print(f'Gamepad lease rejected: {exc}', file=sys.stderr)
                break
            except (OSError, ValueError) as exc:
                print(f'Gamepad API unavailable: {exc}', file=sys.stderr)
                break
            if action == 'off' or not response.get('connected', False):
                print('Gamepad emergency stop. Release and repeat Back + Start to re-enable.')
                session = None
                state.set_owned(False)
    finally:
        if session:
            try:
                api.post('release', {'session': session})
            except (OSError, ValueError, error.HTTPError):
                pass
        pad.close()
        print('Gamepad reader stopped; server/MCU watchdogs also disarm on lost input.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://127.0.0.1:9527',
                        help='Arduino App HTTP endpoint as seen from Linux host')
    parser.add_argument('--device', help='Optional fixed /dev/input/eventN path')
    parser.add_argument('--list', action='store_true', help='List Linux input devices')
    args = parser.parse_args(argv)
    try:
        import evdev
    except ImportError:
        print('Missing evdev on Uno Q Linux host. Install: sudo apt install python3-evdev',
              file=sys.stderr)
        return 2
    if args.list:
        for path in evdev.list_devices():
            try:
                pad = evdev.InputDevice(path)
                print(f'{path}: {pad.name} vendor={pad.info.vendor:04x} '
                      f'usable={usable(pad, evdev)}')
                pad.close()
            except OSError as exc:
                print(f'{path}: {exc}')
        return 0
    alive = [True]
    def stop(_signum, _frame):
        alive[0] = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    api = API(args.api)
    # Bluetooth administration is intentionally separate from the evdev event
    # loop: 12-second scans and pairing never block 20 Hz motion commands.
    from xbox_bluetooth import BluetoothWorker
    import threading
    bt_worker = BluetoothWorker(args.api)

    def bluetooth_supervisor():
        # A fatal BlueZ/programming exception must not leave systemd showing
        # "active" while its only Bluetooth management thread has vanished.
        # The normal HTTP connection-reset path is handled inside run().
        while alive[0]:
            try:
                bt_worker.run(lambda: alive[0])
            except Exception as exc:
                print(f'Bluetooth worker unexpectedly stopped: {exc!r}; '
                      'restarting', file=sys.stderr, flush=True)
            if alive[0]:
                time.sleep(2.0)

    bt_thread = threading.Thread(target=bluetooth_supervisor,
                                 name='xgoduck-bluetooth', daemon=True)
    bt_thread.start()
    missing_logged = False
    while alive[0]:
        pad = find_pad(evdev, args.device)
        if pad is None:
            if not missing_logged:
                print('No readable Xbox event device. Check Bluetooth/USB, '
                      '/dev/input permissions, and --list.', file=sys.stderr)
                missing_logged = True
            time.sleep(1.0)
            continue
        missing_logged = False
        try:
            serve_device(pad, evdev, api, lambda: alive[0])
        except (OSError, ValueError) as exc:
            print(f'Gamepad lost: {exc}', file=sys.stderr)
        if alive[0]:
            time.sleep(0.5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
