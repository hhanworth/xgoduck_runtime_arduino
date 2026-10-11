"""Nonblocking Bluetooth task mailbox between Duck WebUI (container) and Linux host.

Only the host-side worker runs BlueZ operations. Web requests enqueue a small
validated task and return immediately; worker polls and publishes results.
"""
import re
import threading
import time

MAC = re.compile(r'^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$')
ACTIONS = {'scan', 'connect', 'disconnect', 'forget'}
JOB_TIMEOUT = 45.0
WORKER_TIMEOUT = 30.0


class BluetoothRelay:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.job = None
        self.seq = 0
        self.worker_seen = 0.0
        self.snapshot = {'devices': [], 'saved_address': None,
                         'autoconnect': False, 'connected_address': None}
        self.result = ''
        self.error = ''

    def _expire_locked(self, now):
        if self.job and now - self.job['created'] > JOB_TIMEOUT:
            self.error = 'Bluetooth operation timed out; check the Uno Q host service'
            self.job = None

    def request(self, action, address=None):
        if action not in ACTIONS:
            raise ValueError('unknown Bluetooth action')
        if action != 'scan':
            if not isinstance(address, str) or not MAC.fullmatch(address.upper()):
                raise ValueError('invalid Bluetooth device address')
            address = address.upper()
        else:
            address = None
        with self.lock:
            now = self.clock()
            self._expire_locked(now)
            if self.job:
                raise ValueError('another Bluetooth operation is running')
            self.seq += 1
            self.job = dict(id=self.seq, action=action, address=address,
                            created=now, dispatched=False)
            self.result = ''
            self.error = ''
            return {'accepted': True, 'job_id': self.seq}

    def status(self):
        with self.lock:
            now = self.clock()
            self._expire_locked(now)
            data = dict(self.snapshot)
            data.update(online=bool(self.worker_seen and
                                    now - self.worker_seen < WORKER_TIMEOUT),
                        busy=self.job is not None,
                        action=self.job['action'] if self.job else None,
                        result=self.result, error=self.error)
            return data

    def worker_next(self):
        with self.lock:
            now = self.clock()
            self.worker_seen = now
            self._expire_locked(now)
            if self.job and not self.job['dispatched']:
                self.job['dispatched'] = True
                return {'job': {k: self.job[k] for k in ('id', 'action', 'address')}}
            return {'job': None}

    def worker_report(self, body):
        if not isinstance(body, dict):
            raise ValueError('invalid worker response')
        with self.lock:
            self.worker_seen = self.clock()
            if 'devices' in body:
                devices = body['devices']
                if not isinstance(devices, list) or len(devices) > 100:
                    raise ValueError('invalid device list')
                self.snapshot['devices'] = [
                    {'address': str(v.get('address', ''))[:17],
                     'name': str(v.get('name', ''))[:100],
                     'paired': bool(v.get('paired', False)),
                     'connected': bool(v.get('connected', False))}
                    for v in devices if isinstance(v, dict)]
            for key in ('saved_address', 'connected_address'):
                if key in body:
                    self.snapshot[key] = body[key]
            if 'autoconnect' in body:
                self.snapshot['autoconnect'] = bool(body['autoconnect'])
            if 'job_id' in body:
                if self.job is not None and body['job_id'] == self.job['id']:
                    self.result = str(body.get('result', ''))[:200]
                    self.error = str(body.get('error', ''))[:200]
                    self.job = None
        return {'ok': True}
