"""BlueZ Bluetooth management for the Uno Q Linux HOST (not App container).

Runs in a daemon thread next to the evdev reader. Long-running bluetoothctl
calls never run on the FastAPI or 50 Hz policy thread. Paired address is
persisted under the service user's home directory for boot-time reconnect.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib import error, request

MAC_RE = re.compile(r'^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$')
DEVICE_RE = re.compile(r'(?:^|\]\s*)Device\s+([0-9A-Fa-f:]{17})\s+(.+)$')
AUTO_RETRY_S = 30.0
STATUS_PERIOD_S = 8.0


def parse_devices(output):
    found = {}
    for line in output.splitlines():
        # Strip ANSI escapes from interactive scanner output.
        line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line).strip()
        match = DEVICE_RE.search(line)
        if match:
            mac, name = match.groups()
            mac = mac.upper()
            if MAC_RE.fullmatch(mac) and not name.startswith(('RSSI:', 'TxPower:', 'ManufacturerData')):
                found[mac] = name.strip()[:100]
    return found


def parse_info(text):
    return {
        'paired': bool(re.search(r'^\s*Paired:\s+yes\s*$', text, re.M | re.I)),
        'connected': bool(re.search(r'^\s*Connected:\s+yes\s*$', text, re.M | re.I)),
        'trusted': bool(re.search(r'^\s*Trusted:\s+yes\s*$', text, re.M | re.I)),
    }


class BluetoothWorker:
    def __init__(self, api_base, state_path=None, runner=None, clock=time.monotonic):
        self.api_base = api_base.rstrip('/')
        self.clock = clock
        self.runner = runner or subprocess.run
        self.state_path = Path(state_path) if state_path else (
            Path.home() / '.local' / 'state' / 'xgoduck' / 'xbox_bluetooth.json')
        self.saved_address = None
        self.autoconnect = False
        self.scan_cache = {}
        self.last_auto = 0.0
        self.last_status = 0.0
        self.load_state()

    def load_state(self):
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
            mac = str(data.get('address', '')).upper()
            if MAC_RE.fullmatch(mac):
                self.saved_address = mac
                self.autoconnect = bool(data.get('autoconnect', True))
        except (OSError, ValueError, TypeError):
            pass

    def save_state(self):
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = json.dumps({'version': 1, 'address': self.saved_address,
                           'autoconnect': self.autoconnect}, indent=2)
        tmp = self.state_path.with_suffix('.tmp')
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as file:
            file.write(data)
        os.replace(tmp, self.state_path)

    def ctl(self, *args, timeout=10, agent=False):
        cmd = ['bluetoothctl']
        if agent:
            cmd.extend(['--agent', 'NoInputNoOutput'])
        cmd.extend(['--timeout', str(timeout), *args])
        try:
            result = self.runner(cmd, capture_output=True, text=True,
                                 timeout=timeout + 4, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f'BlueZ command failed: {exc}') from exc
        output = (result.stdout or '') + '\n' + (result.stderr or '')
        if result.returncode != 0:
            raise RuntimeError(f'bluetoothctl failed: {output.strip()[:160]}')
        return output

    def info(self, mac):
        return parse_info(self.ctl('info', mac, timeout=5))

    def devices(self):
        found = parse_devices(self.ctl('devices', timeout=5))
        found.update(self.scan_cache)
        if self.saved_address and self.saved_address not in found:
            found[self.saved_address] = 'Saved Xbox controller'
        result = []
        for mac, name in list(found.items())[:35]:
            try:
                state = self.info(mac)
            except RuntimeError:
                state = dict(paired=False, connected=False)
            result.append(dict(address=mac, name=name, paired=state['paired'],
                               connected=state['connected']))
        return result

    def status(self):
        devices = self.devices()
        connected = next((v['address'] for v in devices if v['connected'] and
                          v['address'] == self.saved_address), None)
        return {'devices': devices, 'saved_address': self.saved_address,
                'autoconnect': self.autoconnect, 'connected_address': connected}

    def scan(self):
        self.ctl('power', 'on', timeout=6)
        # A bounded scan. It runs only on this host worker, not the policy thread.
        output = self.ctl('scan', 'on', timeout=12)
        self.scan_cache.update(parse_devices(output))
        return 'Scan finished'

    def connect(self, mac):
        state = self.info(mac)
        if not state['paired']:
            # Explicit user request only. Never silently pair unknown devices.
            self.ctl('pair', mac, timeout=22, agent=True)
            state = self.info(mac)
            if not state['paired']:
                raise RuntimeError('Pairing failed; put the controller into Bluetooth pairing mode')
        if not state['trusted']:
            self.ctl('trust', mac, timeout=8)
        if not state['connected']:
            self.ctl('connect', mac, timeout=15)
        state = self.info(mac)
        if not state['connected']:
            raise RuntimeError('Xbox Bluetooth connection not established')
        self.saved_address = mac
        self.autoconnect = True
        self.save_state()
        return 'Xbox connected; remembered for automatic reconnect'

    def disconnect(self, mac):
        self.ctl('disconnect', mac, timeout=10)
        if mac == self.saved_address:
            self.autoconnect = False  # Explicit disconnect must stay disconnected.
            self.save_state()
        return 'Disconnected; automatic reconnect paused'

    def forget(self, mac):
        self.ctl('remove', mac, timeout=10)
        if mac == self.saved_address:
            self.autoconnect = False
            self.saved_address = None
            self.save_state()
        self.scan_cache.pop(mac, None)
        return 'Pairing removed and automatic reconnect disabled'

    def execute(self, job):
        action = job['action']
        mac = job.get('address')
        if action not in ('scan', 'connect', 'disconnect', 'forget'):
            raise ValueError('unsupported Bluetooth action')
        if action != 'scan' and (not isinstance(mac, str) or
                                  not MAC_RE.fullmatch(mac)):
            raise ValueError('invalid Bluetooth address')
        return getattr(self, action)(*([mac] if mac else []))

    def _http(self, method, path, data=None):
        body = json.dumps(data).encode('utf-8') if data is not None else None
        req = request.Request(self.api_base + '/api/bluetooth/worker/' + path,
                              data=body, method=method,
                              headers={'Content-Type': 'application/json'})
        with request.urlopen(req, timeout=2) as response:
            return json.load(response)

    def report(self, **kwargs):
        return self._http('POST', 'report', kwargs)

    def autoconnect_step(self):
        now = self.clock()
        if not self.saved_address or not self.autoconnect or (
                self.last_auto and now - self.last_auto < AUTO_RETRY_S):
            return False
        self.last_auto = now
        try:
            state = self.info(self.saved_address)
            # No automatic pairing! Only a previously paired device is eligible.
            if not state['paired']:
                return False
            if not state['connected']:
                self.ctl('connect', self.saved_address, timeout=15)
            return self.info(self.saved_address)['connected']
        except RuntimeError:
            return False

    def run(self, running):
        try:
            self.ctl('power', 'on', timeout=6)
        except RuntimeError as exc:
            print(f'Bluetooth power on failed: {exc}', flush=True)
        # Try restoring previous pairing even before App Lab has started.
        self.autoconnect_step()
        while running():
            try:
                response = self._http('GET', 'next')
                job = response.get('job')
                if job:
                    try:
                        result = self.execute(job)
                        error_text = ''
                    except (ValueError, RuntimeError, OSError) as exc:
                        result, error_text = '', str(exc)
                    try:
                        self.report(**self.status(), job_id=job['id'],
                                    result=result, error=error_text)
                    except (error.URLError, TimeoutError, ValueError) as exc:
                        print(f'Bluetooth result report failed: {exc}', flush=True)
                else:
                    now = self.clock()
                    # Auto-reconnect and status discovery are on host thread only.
                    self.autoconnect_step()
                    if not self.last_status or now - self.last_status >= STATUS_PERIOD_S:
                        self.report(**self.status())
                        self.last_status = now
            except (error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
                # App may not be started yet; do not block evdev input service.
                print(f'Bluetooth worker retrying: {exc}', flush=True)
            # Systemd manages process restarts; worker polls independently of UI.
            time.sleep(1.0)
