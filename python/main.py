import threading
import sys
from fastapi import HTTPException
from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI
from controller import Controller
from bluetooth_relay import BluetoothRelay

# Bound GIL handoff latency between the 100 Hz receiver, policy and WebUI.
sys.setswitchinterval(0.001)
controller = Controller(Bridge)
bluetooth = BluetoothRelay()
ui = WebUI(port=9527, cors_origins='')
Bridge.provide('duck_state', controller.receive)
Bridge.provide('duck_boot_pd', controller.receive_boot_pd)
Bridge.provide('duck_raw', controller.receive_raw)
Bridge.provide('duck_servo_reply', controller.receive_servo_reply)
Bridge.provide('duck_battery', controller.receive_battery)

def mode(body: dict):
    try: return controller.set_mode(body['mode'])
    except (KeyError, ValueError) as exc: raise HTTPException(400, str(exc))

def command(body: dict):
    try: return controller.command(**body)
    except (TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def filtering(body: dict):
    try:
        leg, head = controller.set_action_alphas(**body)
        return {'leg':leg,'head':head}
    except (TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def pick(_body: dict = None):
    try: return controller.start_pick()
    except ValueError as exc: raise HTTPException(400, str(exc))

def roulade(_body: dict = None):
    try: return controller.start_roulade()
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_enter(_body: dict = None):
    try: return controller.set_mode('calibrate')
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_exit(_body: dict = None):
    try: return controller.set_mode('shadow')
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_targets(body: dict):
    try: return controller.cal_targets_set(body['targets'])
    except (KeyError, TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def cal_finish(body: dict):
    try: return controller.cal_finish(body['index'])
    except (KeyError, TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def servo_enter(_body: dict = None):
    try: return controller.set_mode('servo_debug')
    except ValueError as exc: raise HTTPException(400, str(exc))

def servo_exit(_body: dict = None):
    try: return controller.set_mode('shadow')
    except ValueError as exc: raise HTTPException(400, str(exc))

def servo_action(body: dict):
    try:
        return controller.servo_op(
            body['action'], body['target_id'],
            new_id=body.get('new_id'), kp=body.get('kp', 5), kd=body.get('kd', 20),
            raw_pos=body.get('raw_pos', 2047))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

def gamepad_claim(_body: dict = None):
    try: return controller.gamepad_claim()
    except ValueError as exc: raise HTTPException(409, str(exc))

def gamepad_input(body: dict):
    try:
        return controller.gamepad_input(body['session'], body['twist'], body['head'],
                                        body['mouth'], body.get('action'))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, str(exc))

def gamepad_release(body: dict):
    try: return controller.gamepad_release(body['session'])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

def bluetooth_scan(_body: dict = None):
    try: return bluetooth.request('scan')
    except ValueError as exc: raise HTTPException(409, str(exc))

def bluetooth_action(body: dict):
    try:
        action = body['action']
        if action in ('disconnect', 'forget'):
            # Immediately revoke gamepad motion authority before unplugging it.
            if controller.input_owner == 'gamepad':
                controller.set_mode('off')
        return bluetooth.request(action, body.get('address'))
    except (KeyError, ValueError) as exc:
        raise HTTPException(409, str(exc))

def bluetooth_worker_report(body: dict):
    try: return bluetooth.worker_report(body)
    except ValueError as exc: raise HTTPException(400, str(exc))

ui.expose_api('GET', '/api/status', controller.status)
ui.expose_api('GET', '/api/bluetooth/status', bluetooth.status)
ui.expose_api('POST', '/api/bluetooth/scan', bluetooth_scan)
ui.expose_api('POST', '/api/bluetooth/action', bluetooth_action)
ui.expose_api('GET', '/api/bluetooth/worker/next', bluetooth.worker_next)
ui.expose_api('POST', '/api/bluetooth/worker/report', bluetooth_worker_report)
ui.expose_api('POST', '/api/mode', mode)
ui.expose_api('POST', '/api/gamepad/claim', gamepad_claim)
ui.expose_api('POST', '/api/gamepad/input', gamepad_input)
ui.expose_api('POST', '/api/gamepad/release', gamepad_release)
ui.expose_api('POST', '/api/command', command)
ui.expose_api('POST', '/api/filter', filtering)
ui.expose_api('POST', '/api/pick', pick)
ui.expose_api('POST', '/api/roulade', roulade)
ui.expose_api('POST', '/api/cal/enter', cal_enter)
ui.expose_api('POST', '/api/cal/exit', cal_exit)
ui.expose_api('POST', '/api/cal/targets', cal_targets)
ui.expose_api('POST', '/api/cal/finish', cal_finish)
ui.expose_api('POST', '/api/servo/enter', servo_enter)
ui.expose_api('POST', '/api/servo/exit', servo_exit)
ui.expose_api('POST', '/api/servo', servo_action)
worker = threading.Thread(target=controller.run, name='policy50', daemon=True)
worker.start()
try:
    App.run()
finally:
    controller.stop()
    worker.join(timeout=2)
