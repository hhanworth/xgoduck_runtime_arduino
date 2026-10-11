"""Bluetooth host/relay tests using synthetic BlueZ outputs (no adapter required)."""
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from bluetooth_relay import BluetoothRelay
from xbox_bluetooth import BluetoothWorker, parse_devices, parse_info

MAC = '12:34:56:78:9A:BC'


class SimBlueZ:
    def __init__(self):
        self.paired = False
        self.trusted = False
        self.connected = False
        self.calls = []
        self.fail_pair = False

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        args = cmd[cmd.index('--timeout') + 2:]
        op = args[0]
        if op == 'info':
            data = 'Device ' + MAC + '\n    Paired: ' + ('yes' if self.paired else 'no')
            data += '\n    Trusted: ' + ('yes' if self.trusted else 'no')
            data += '\n    Connected: ' + ('yes' if self.connected else 'no')
        elif op == 'devices':
            data = 'Device ' + MAC + ' Xbox Wireless Controller'
        elif op == 'scan':
            data = '[NEW] Device ' + MAC + ' Xbox Wireless Controller'
        else:
            data = op + ' successful'
            if op == 'pair' and not self.fail_pair:
                self.paired = True
            if op == 'trust':
                self.trusted = True
            if op == 'connect' and self.paired:
                self.connected = True
            if op == 'disconnect':
                self.connected = False
            if op == 'remove':
                self.connected = self.trusted = self.paired = False
        return subprocess.CompletedProcess(cmd, 0, stdout=data, stderr='')

    def count(self, op):
        return sum(1 for cmd in self.calls if op in cmd)


class BluetoothWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'pairing.json'
        self.blue = SimBlueZ()
        self.worker = BluetoothWorker('http://127.0.0.1:9527',
                                      state_path=self.path, runner=self.blue)

    def test_parser(self):
        names = parse_devices('[NEW] Device ' + MAC + ' Xbox Controller\n'
                              '[CHG] Device ' + MAC + ' RSSI: -70')
        self.assertEqual(names[MAC], 'Xbox Controller')
        self.assertTrue(parse_info(' Paired: yes\n Connected: yes')['connected'])
        self.assertFalse(parse_info(' Paired: no\n Connected: no')['paired'])

    def test_scan_and_async_job_dispatch_protocol(self):
        clock = [10.0]
        relay = BluetoothRelay(clock=lambda: clock[0])
        response = relay.request('scan')
        self.assertTrue(response['accepted'])
        self.assertEqual(relay.worker_next()['job']['action'], 'scan')
        self.assertIsNone(relay.worker_next()['job'])
        self.assertEqual(self.worker.scan(), 'Scan finished')
        relay.worker_report({'job_id': response['job_id'],
                             'devices': self.worker.devices(), 'result': 'Scan finished'})
        self.assertFalse(relay.status()['busy'])
        self.assertTrue(relay.status()['online'])
        self.assertEqual(relay.status()['devices'][0]['name'], 'Xbox Wireless Controller')

    def test_pair_connect_remember_boot_reconnect(self):
        self.worker.connect(MAC)
        self.assertTrue(self.blue.paired)
        self.assertTrue(self.blue.trusted)
        self.assertTrue(self.blue.connected)
        self.assertEqual(self.worker.saved_address, MAC)
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.blue.connected = False
        restarted = BluetoothWorker('http://127.0.0.1:9527',
                                    state_path=self.path, runner=self.blue,
                                    clock=lambda: 100.0)
        self.assertEqual(restarted.saved_address, MAC)
        self.assertTrue(restarted.autoconnect_step())
        self.assertTrue(self.blue.connected)
        self.assertEqual(self.blue.count('pair'), 1)  # never re-pair on reboot

    def test_manual_disconnect_suspends_auto(self):
        self.worker.connect(MAC)
        self.worker.disconnect(MAC)
        self.assertFalse(self.worker.autoconnect)
        self.assertFalse(self.blue.connected)
        restarted = BluetoothWorker('http://127.0.0.1:9527',
                                    state_path=self.path, runner=self.blue)
        self.assertFalse(restarted.autoconnect_step())
        self.assertFalse(self.blue.connected)
        restarted.connect(MAC)
        self.assertTrue(restarted.autoconnect)
        self.assertEqual(self.blue.count('pair'), 1)

    def test_forget_clears_state(self):
        self.worker.connect(MAC)
        self.worker.forget(MAC)
        self.assertIsNone(self.worker.saved_address)
        self.assertFalse(self.worker.autoconnect)
        self.assertFalse(self.blue.paired)

    def test_unpaired_boot_does_not_attempt_pair(self):
        self.worker.saved_address = MAC
        self.worker.autoconnect = True
        self.assertFalse(self.worker.autoconnect_step())
        self.assertEqual(self.blue.count('pair'), 0)
        self.assertEqual(self.blue.count('connect'), 0)

    def test_pair_failure_not_saved(self):
        self.blue.fail_pair = True
        with self.assertRaises(RuntimeError):
            self.worker.connect(MAC)
        self.assertIsNone(self.worker.saved_address)
        self.assertFalse(self.path.exists())

    def test_systemd_state_directory(self):
        with patch.dict(os.environ, {'STATE_DIRECTORY': self.tmp.name}):
            worker = BluetoothWorker('http://localhost', runner=self.blue)
            self.assertEqual(worker.state_path.parent, Path(self.tmp.name))


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tick = [10.0]
        self.relay = BluetoothRelay(clock=lambda: self.tick[0])

    def test_nonblocking_busy_and_validation(self):
        self.relay.request('scan')
        self.assertTrue(self.relay.status()['busy'])
        with self.assertRaises(ValueError):
            self.relay.request('scan')
        with self.assertRaises(ValueError):
            self.relay.request('connect', 'not a mac')
        self.assertEqual(self.relay.worker_next()['job']['action'], 'scan')
        self.assertIsNone(self.relay.worker_next()['job'])
        # Worker already polled this job; web status stays busy.
        self.assertEqual(self.relay.status()['action'], 'scan')

    def test_stale_worker_and_job_expiry(self):
        self.relay.request('scan')
        self.assertFalse(self.relay.status()['online'])
        self.relay.worker_next()
        self.tick[0] += 46.0
        self.assertFalse(self.relay.status()['busy'])
        self.assertIn('timed out', self.relay.status()['error'])
        self.assertFalse(self.relay.status()['online'])

    def test_invalid_late_result_cannot_clear_new_job(self):
        old = self.relay.request('scan')['job_id']
        self.tick[0] += 46.0
        new = self.relay.request('connect', MAC)['job_id']
        self.relay.worker_report({'job_id': old, 'error': 'old error'})
        self.assertTrue(self.relay.status()['busy'])
        self.assertEqual(self.relay.worker_next()['job']['id'], new)
        self.relay.worker_report({'job_id': new, 'result': 'connected',
                                  'saved_address': MAC, 'autoconnect': True})
        self.assertFalse(self.relay.status()['busy'])
        self.assertEqual(self.relay.status()['saved_address'], MAC)


if __name__ == '__main__':
    unittest.main()
