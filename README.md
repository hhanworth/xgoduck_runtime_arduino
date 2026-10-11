# XGO Duck

This project runs walking, get-up, pick, and roulade policies on an XGODuck built around the Arduino Uno Q.

It is based on [Microduck](https://github.com/pollen-robotics/microduck) by [Pollen Robotics](https://pollen-robotics.com/microduck/).

The policies here follow that project: 15 servos, a 50 Hz neural controller, and the same joint order.

## What runs where

The Uno Q is two computers.

| | Host | MCU |
| --- | --- | --- |
| Silicon | Qualcomm Linux | STM32U585, Zephyr |
| Rate | 50 Hz policy | 100 Hz bus loop |
| Work | ONNX Runtime, one thread; web UI on port 9527 | 15-servo bus, QMI8658, Bridge report thread |
| Link | Official Arduino App container | `Bridge` at 2 Mbps |

The MCU loop does not wait for Linux. It publishes a 240-byte `duck_state` snapshot at 100 Hz. The host filters each snapshot on arrival and the 50 Hz thread reads the latest one. A late cycle is skipped. There is no queued backlog of actions.

```text
browser :9527
    |
Linux  50 Hz ONNX  ---- duck_command (80 bytes) ---->  STM32 100 Hz
    ^                                                      |
    +------------- duck_state (240 bytes) -----------------+
                                                           |
                                              Serial1 1 Mbps, IDs 10-14, 20-24, 30-34
                                              Wire, QMI8658
```

Joint angles, rates, and targets are float32 degrees. Servo positions are saturated to the encoder range 0–4095. Non-finite targets are rejected. A finite angle outside ±180° is not rejected.

## Servo map

IDs follow Microduck. The bus always addresses all 15, in this order:

| IDs | Role |
| --- | --- |
| 10–14 | Leg |
| 20–24 | Leg |
| 30–33 | Neck and head. Policy order inside the 14-D action is 10–14, 30–33, 20–24. |
| 34 | Mouth. It is not part of the policy action. The web UI commands it from 0° to 30°. |

The home pose in `sketch/duck_config.h` and `python/rl_core.py` is hip/ankle ±23°, knee 0°, hip roll ±5°, neck and head pitch 20°, mouth 0°. `action_scale` is 1. Home is this table, not an ONNX metadata field.

Factory encoder zeros are `ZERO_POS` in `sketch/duck_config.h`. A calibration overwrites them in `data/zero_pos.json` and the host copies that file into MCU RAM at startup. `data/zero_pos.json` is local to the robot and is gitignored.

## First-time servo setup

Do this before the servos are installed in the frame. Connect **one servo at a time**. Two servos with the same ID on the bus will both accept a write.

1. Open **Servo setup** (`/servo.html`) and enter setup.
2. Assign IDs **10–14** and **20–24** for the legs. Assign **30–34** for the neck, head, and mouth. The ID write unlocks register `0x37` and then writes `0x05`.
3. Center that servo. **Move to** raw position **2047** and write it. Temporary run gains for this move are KP = 6 and KD = 20. ID 34 uses temporary KP = 10.
4. Write stored KP/KD. The page defaults are **KP = 5** and **KD = 20**. The write unlocks `0x37` and stores both bytes at `0x15`.
5. Disconnect that servo and repeat until every ID exists exactly once.
6. Install the servos in the frame and finish the wiring: servo bus on D0/D1 (`Serial1`, 1 Mbps), QMI8658 on D20/D21 (`Wire`, 400 kHz, address `0x6A` then `0x6B`).

After power-up the controller also writes the temporary torque register (address 40) and temporary KP/KD (addresses 50 and 51): KP = 6, KD = 20, and KP = 10 on ID 34. Torque off clears torque and does not write a goal position. Writing a goal after torque-off still drags a connected servo, so position writes are suppressed while disarmed.

## Calibration

Do this after the harness is wired and every ID answers.

1. Open **Calibration** (`/calibrate.html`) and start it. Policy and pose control stop. Temporary gains become KP = 3 and KD = 0, and every servo is commanded to 2047.
2. For each joint, set the slider so the link sits at its mechanical zero, then press **Finish**. The current encoder reading becomes that joint's zero and is saved to `data/zero_pos.json`.
3. Exit calibration. The highlight on a finished row is only for this visit; the saved zero is the encoder value.

## Running

Pinned stack: App CLI with Arduino Zephyr core 1.0.0, RouterBridge 0.4.3, RPClite 0.3.1, MsgPack 0.4.2. `sketch/sketch.yaml` lists those libraries, including the transitive ones. Python packages are installed by the App build from `python/requirements.txt`.

On the Uno Q, from this directory:

```bash
python3 tools/prepare-bridge.py
sudo bash tools/router-2m.sh
arduino-app-cli app start "$PWD"
```

`prepare-bridge.py` patches the resolved library cache and keeps the original headers as `.h.original`:

- RouterBridge 0.4.3: RPC thread stack 500 → 4096 bytes.
- RPClite 0.3.1: a bounded MessagePack scanner accepts a complete frame before `Unpacker::feed()`, and rejects a frame larger than the RPC buffer.

Re-run the script after a library-cache wipe or on a new board. If it cannot find the libraries, let the App CLI resolve `sketch.yaml` once, then run it again.

`router-2m.sh` sets the router serial rate to 2 Mbps in `/etc/systemd/system/arduino-router.service.d/90-xgoduck-baud.conf` and leaves the board GPIO ready-hook in place. That rate is global. Before starting another app that calls `Bridge.begin()` at 115200, move the drop-in aside, reload systemd, and restart `arduino-router`. Both ends must use the same rate. Restarting the router recreates its socket and resets the MCU, so stop this app first, restart the router, then start the app again. A normal app restart does not need a router restart.

If an isolated CLI build tries to re-download a toolchain that is already on the board and GitHub is unreachable, run `python3 tools/reuse-toolchain.py` and start the app again.

GCC 14 can tail-call `k_thread_name_set` at the end of `setup()` with a Thumb `JUMP24` from the loadable sketch into flash. The Uno Q loader then returns ENOEXEC (−8) and the MCU never starts. The `asm volatile("nop")` at the end of `setup()` keeps a long call. `python3 tools/verify-elf.py` checks the built ELF for the same class of short branch to an unresolved firmware symbol.

The web UI is served on port 9527. It includes both the browser Xbox Gamepad API and the Uno Q host's Bluetooth management panel; these are separate control paths. Native host Xbox ownership has priority over browser input. App Lab starts the same app under the name in `app.yaml`.

The app boots in **inference only**: the network runs, torque stays off. From the control page:

| Control | Effect |
| --- | --- |
| Inference only | Policy runs. Torque off. |
| Default pose | Holds the home pose. Requires a fresh MCU and IMU sample. |
| Walk / get up | Enables position control and the recovery state machine. |
| Pick | While upright in walk, runs `xgoduck_pick.onnx` for 4 s, then returns to walk. |
| Roulade | While upright in walk, runs `xgoduck_roulade.onnx` for 1.9 s, then returns to walk. |
| Torque off | Disables position control. |

For browser/keyboard/gamepad control, closing the page or leaving it in the background turns position control off. An independently claimed Uno Q host Xbox lease continues operating without a browser; the Web Torque off button always revokes that lease. Calibration and servo setup use a 2 second heartbeat and then exit and turn torque off.

Walk commands are vx ±0.4, vy ±0.3, ωz ±1.0, with a joystick dead zone of 0.1. Head channels are four radians in ±1. Keyboard: W/S, A/D, Q/E, Space.

## Xbox gamepad control (browser)

Connect a standard-layout Xbox controller over USB or Bluetooth to the computer/phone running the XGO Duck control page. Open the control page, press a gamepad button so the browser discovers it, release both sticks and click **Enable gamepad**. Browser Gamepad API support is required; for remote hosts some browsers require HTTPS (or localhost). The Xbox pad is not paired directly to the Uno Q in this mode. No extra Python packages or firmware modifications are required.

| Input | Action |
| --- | --- |
| Left stick | Forward/backward (vx ±0.4), strafe (vy ±0.3) |
| Right stick X | Yaw (ωz ±1.0) |
| Hold LB | Replace walking input with head channels 1–4 (left X/Y, right X/Y) |
| RT | Mouth 0–30° |
| A | Default pose |
| Start | Walk / get-up |
| X | Pick (upright walk only) |
| Y | Roulade (upright walk only) |
| B | Emergency torque off |

The gamepad is **opt-in** and never arms automatically on connection. Deadzone 0.12 uses rescaling outside the deadzone. Face buttons are edge-triggered. Disconnecting the gamepad, backgrounding the page, or blurring the browser window disables control and sends torque-off. Host/MCU watchdogs still apply. Before testing with the robot powered, validate mappings and button directions with torque off, then test with the robot supported safely.


## Direct USB/Bluetooth Xbox controller (no browser)

This mode runs the Xbox reader **on the Uno Q Debian Linux host**, not in
the Arduino App Lab container. App Lab runs Python in Docker; by default it
cannot be assumed to see host `/dev/input/event*` devices. The host helper
uses `python-evdev` for the kernel input event API and sends its commands
through **localhost HTTP** to the running App. No new sketch protocol or
ONNX dependencies are required.

```text
Xbox USB / Bluetooth → UNO Q host /dev/input/eventN
  → tools/xbox_host.py (20 Hz, edge-triggered buttons)
  → localhost:9527/api/gamepad/{claim,input,release}
  → python/controller.py (exclusive lease + 350 ms watchdog)
  → 50 Hz ONNX inference → Arduino Bridge → STM32 100 Hz → motors
```

### Setup on the UNO Q Debian host

1. Start the existing Duck App as in **Running** above. The MCU, ONNX
   models and web UI remain part of the app. You do **not** have to open
   the webpage after starting the App.
2. Pair the Xbox controller with the **UNO Q**, not with your laptop:
   - **USB:** use a USB-C OTG host adapter or powered USB-C hub. Confirm
     `lsusb` detects the controller. Host mode may depend on the OS image
     and USB-C power/host setup; see the official
     [UNO Q user manual](https://docs.arduino.cc/tutorials/uno-q/user-manual/).
   - **Bluetooth:** on the UNO Q host, run `bluetoothctl`; then
     `power on`, `agent on`, `default-agent`, `scan on`. Put the
     controller in pairing mode and run `pair MAC`, `trust MAC`,
     `connect MAC`. Some older Xbox One controllers lack Bluetooth.
3. Install evdev on the **Linux host** (not in `python/requirements.txt`):
   ```bash
   sudo apt update
   sudo apt install python3-evdev
   python3 tools/xbox_host.py --list
   ```
   If `--list` shows permission denied for `/dev/input/event*`, run
   `ls -l /dev/input/event*` and grant the service account access
   to the appropriate input device group. On default Debian installs
   this is often `input`:
   `sudo usermod -aG input "$USER"` then **log out and back in**.
   Membership of the `input` group gives broad raw input access;
   on shared devices prefer a narrowly scoped udev rule instead.
4. Verify the App is reachable **from the UNO Q host**:
   ```bash
   curl -f http://127.0.0.1:9527/api/status
   python3 tools/xbox_host.py
   ```
   If a nonstandard controller name is not auto-detected, pass
   `--device /dev/input/eventN` for the correct event node shown by
   `--list`. Device nodes may change after reboot; automatic device
   discovery is preferable. Run the reader with a terminal multiplexer
   or as a systemd service to make it persistent. An optional sample unit
   is in `tools/xgoduck-xbox.service.example`; edit its absolute path
   and service user before installing.

### Web-managed Bluetooth pairing and boot reconnect

The **Xbox 蓝牙手柄** panel on the normal Duck control webpage now
manages the **Uno Q Linux host's Bluetooth adapter**, not the Bluetooth
adapter on the browser's computer/phone. It can:

- **Scan devices** (roughly 12 seconds), showing discovered Bluetooth
  addresses and names. The operation is queued and runs only on the
  Linux host's background worker; HTTP and 50 Hz inference continue.
- **Pair and connect** a selected Xbox controller. On the controller,
  hold its pairing button until the Xbox indicator flashes rapidly.
  If the controller was already paired, connect without pairing again.
- **Disconnect** (pauses automatic reconnect), or **Forget** (removes
  BlueZ pairing and saved preference).
- Display connection, pairing, background progress, and service status.

After a successful pairing, the background service saves the MAC
address and `autoconnect` flag at the path determined by
`$STATE_DIRECTORY/xbox_bluetooth.json` (systemd's `StateDirectory`
setting), or `~/.local/state/xgoduck/xbox_bluetooth.json` when
run manually. BlueZ separately persists Bluetooth bond keys.
**On the next boot**, when the service starts, it checks that the
remembered device is still paired and tries to connect it, then
retries about every 30 seconds when disconnected. A deliberate UI
disconnect disables these retries until you reconnect manually.
Automatic Bluetooth reconnection **never** arms motors or claims the
gamepad lease: you must still release Back/Start and hold the chord
for one second before requesting default pose or walk.

#### Enable background service at boot

Install BlueZ and evdev on the Uno Q Debian Linux **host**:

```bash
sudo apt update
sudo apt install bluez python3-evdev
sudo systemctl enable --now bluetooth
bluetoothctl list
python3 tools/xbox_host.py --list
```

The existing Arduino App (HTTP port 9527) must also be running for
web scans and on-screen connection state. The host BlueZ worker can
attempt reconnect before the App starts. Start the App as documented
above and use a **separate** host service for Xbox input + Bluetooth.

A sample systemd unit is
`tools/xgoduck-xbox.service.example`. Copy it to
`/etc/systemd/system/xgoduck-xbox.service`, editing **User**,
**WorkingDirectory**, and the absolute **ExecStart** path to match the
actual repository location (the sample paths are placeholders). Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xgoduck-xbox.service
sudo systemctl status xgoduck-xbox.service
journalctl -u xgoduck-xbox.service -f
```

The sample service uses `StateDirectory=xgoduck`; the saved pairing
preference lives in systemd's persistent state directory, typically
`/var/lib/xgoduck/xbox_bluetooth.json`. It grants access to the
`input` group. Do not simultaneously run another copy of
`tools/xbox_host.py` against the same gamepad.

**Security:** The existing web API is not authenticated. Bluetooth
pairing and trust should only be exposed on a trusted network or
behind an authenticated reverse proxy. The background service does
not start robot movement on connection, and a browser cannot override
an active native gamepad control lease except with Torque off.

### Linux Xbox input layouts and diagnostics

Microsoft's Xbox Bluetooth HID driver can expose a different axis map from
the USB `xpad` driver. `tools/xbox_host.py` now auto-detects both:

| Linux input layout | Left stick | Right stick | Right trigger |
| --- | --- | --- | --- |
| `xpad` (common USB) | `ABS_X/ABS_Y` | `ABS_RX/ABS_RY` | `ABS_RZ` |
| `bluetooth-hid` | `ABS_X/ABS_Y` | `ABS_Z/ABS_RZ` | `ABS_GAS` |

For the hand-held Xbox device that shows `vendor=045e usable=False`:

```bash
cd ~/ArduinoApps/xgoduck_runtime_arduino_
git pull --ff-only origin feature/xbox-linux-input
sudo systemctl restart xgoduck-xbox.service
python3 tools/xbox_host.py --list
```

A correctly matched device now shows `usable=True layout=bluetooth-hid`
or `usable=True layout=xpad`. The `--list` command also prints axis
names and missing safety-critical buttons to assist with unsupported
kernel mappings. The reader does not guess between short-range
trigger axes and full-range sticks; an ambiguous mapping stays
`usable=False` until diagnosed. This command is read-only and can run
while the service is active.

Confirm that the reader reports an input device in
`journalctl -u xgoduck-xbox.service -n 30 --no-pager`. Then release
Back/Start and hold them together for one second with centered sticks.
Claiming the input lease never activates servo torque by itself.
Keep the robot supported and test A/default pose before Start/walk.

### Xbox buttons and safety

| Input | Action |
| --- | --- |
| **Back + Start held 1 second** | Claim input lease **with torque still off**; must release and re-hold after disconnect/stop |
| **A** | Default pose (arm only when fresh MCU + IMU) |
| **Start** | Walk / recovery policy (arm only when fresh MCU + IMU) |
| Left stick Y / X | Forward and strafe (vx ±0.4, vy ±0.3) |
| Right stick X | Yaw (ωz ±1.0) |
| Hold LB + left/right sticks | Head channels 1–4 (±1), walking twist zero |
| RT | Mouth 0–30° |
| X / Y | Pick / roulade, subject to existing recovery interlocks |
| **B** | **Immediate torque off; lease revoked** |

Before claiming, **release Back/Start once**, then hold the chord with the
sticks at neutral; controller rearm is never automatic. Once claimed, the
physical pad has exclusive motion ownership. Web motion commands and mode
changes are rejected; **web Torque off always overrides the pad**.
Browser backgrounding does not disable an active native Xbox session.

The Linux reader sends 20 Hz heartbeats even with neutral sticks.
Disconnect, input synchronization loss, daemon termination, or network
errors end the session. The host controller revokes a session if input
is absent for **350 ms**; the existing MCU command watchdog is still
**250 ms** (with independent source-age and IMU checks). A stale MCU/IMU
feedback condition also revokes the lease. Following a stop, the hand-held
controller must perform the Back + Start chord again.

**Initial bring-up:** keep the robot safely supported with its feet clear
of the floor. First run `--list`, verify key/axis mapping with motors
disarmed, then enable default pose before testing walk. Never rely solely
on the B button: keep a power disconnect accessible. App HTTP endpoints
are not authenticated for remote networks; use the existing control page
and host API only on a trusted, firewalled network.

### Tests

From a normal development machine (Python 3.11+ and NumPy):

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

These unit tests use synthetic Linux input events and a fake Bridge. They
do not require a physical gamepad, evdev, ONNX files, or a running motor.

## Policies

Models in `python/`:

- `xgoduck_walk.onnx`
- `xgoduck_getup.onnx`
- `xgoduck_pick.onnx`
- `xgoduck_roulade.onnx`

Each expects observation `[1, 61]` and emits action `[1, 14]`. All four are loaded and warmed at startup. Spare time in the 50 Hz loop runs one dummy pass on an idle session so a switch does not pay a cold start.

Observation layout: gyro (rad/s), gravity, 14 joint positions relative to home, 14 joint velocities, previous action, twist (3), head (4), and six zeros.

Recovery, evaluated on the 50 Hz clock:

- Tilt above 55° for 0.15 s → get-up model, action forced to 0 for 1 s, then the get-up action. Twist and head are zero.
- Tilt below 15° for 1 s → walk, and the web twist/head/mouth commands apply again.
- Pick, only from upright walk: command `[cos(2πφ), sin(2πφ), 0]` plus ten zeros, `φ` advances by `dt/4` (a 4 s cycle). The mouth opens to 30° and closes when φ ≥ 0.4.
- Roulade, only from upright walk: runs for 1.9 s with twist, head, and the six trailing command inputs at 0. Gyro, gravity, joint position, joint velocity, and the previous action stay on the usual path. Leg and neck smoothing drop to α = 0.15 for that window, then return to the previous values. Tilt does not select get-up until the 1.9 s ends.

Action smoothing uses α = 0.45 on slices `[0:5]`, `[5:10]`, and `[10:14]`. `prev_action` stores the raw network output and is cleared on a policy switch. The 1 s fallen hold sends the home pose directly and does not keep a filtered walk action. Gyro smoothing α = 0.5, joint-velocity α = 0.4, gravity complementary-filter τ = 0.3 s. Accelerometer correction falls off when the norm leaves 1 g by more than about 20%. There is no gyro bias subtraction. The QMI axes are mapped once, in firmware; the host undoes the reference device mapping so the filter matches the Microduck math without applying that mapping twice.

Position control stops if commands are absent for 250 ms or the IMU is absent for 100 ms. Enabling again takes an explicit mode change. Host feedback older than 150 ms also disarms; that matches the MCU source-age limit.

## Battery estimate (servo rail)

The STM32 polls **one responding STS/SCS servo per second** on the existing 1 Mbps serial bus.
The requested register is `0x3E` (present voltage, **0.1 V/count**); the chosen servo rotates
among IDs reporting position feedback. A separate 1.5 ms bounded reply wait runs **after**
the 100 Hz position read, so it does not overlap with the regular sync-read traffic.
No extra fuel gauge, ADC wiring, or Linux GPIO is needed.

As on [Microduck](https://github.com/pollen-robotics/microduck/blob/main/duck-control/src/model.rs),
the voltage-to-charge estimate is linear: **6.6 V = 0%** and **8.2 V = 100%**,
clamped at the endpoints. These are usable voltages for an unregulated **2S Li-ion
servo rail under load**, *not* a cell-accurate state of charge. The initial successful
sample is used immediately; later valid samples are smoothed with an EMA
(alpha = 0.25 per approximately 1 s reading) to reduce load-related jumps.
A missing reply or a nonsensical reading (outside 4.0–14.0 V) is ignored.
After **5 seconds without a valid reply**, the MCU clears the estimate to *unknown*.
A 5 V bench supply, by contrast, is valid and displays 0% under this mapping.
Adjust the thresholds in `sketch/battery.h` if your servo power rail differs.

**Local indicators (continue working without Linux):**

- Uno Q **8×13 blue LED matrix** shows a battery outline with up to 8 fill columns.
  No valid reading shows a question mark. At ≤10% the fill blinks.
- Uno Q **STM RGB LED 4** (active-low `LED4_R/G/B`) shows green ≥60%,
  yellow from 25–59%, red below 25%, flashing red at ≤10%.
  No valid measurement flashes blue. The QRB-controlled LEDs are left untouched.

Every 100 MCU state reports (~1 s), `duck_battery` carries a separate
**8-byte `DQB1` packet** (little endian: 4-byte magic, uint16 millivolts,
uint8 percentage, uint8 flags, bit 0 = valid).
The 240-byte `duck_state` ABI and the 100 Hz motion controller are unchanged.
The host exposes `battery.valid`, `battery.voltage_v`, and `battery.percent`
in `/api/status` and the **control Web UI** shows voltage, percentage and a level
bar. The Calibration and Servo setup pages also show a compact battery readout. The host treats packets older than 3 seconds as unknown; it never displays
unavailable telemetry as 0%.

For a protocol-only check run `python3 -m unittest discover -s tests -v`.
**Hardware verification is still required** on an Uno Q with the actual battery
and servos: check the register returns usable voltage, indicator orientation,
bus-loop overruns, color thresholds, and timeout-to-unknown behavior with
servo power disconnected. No such physical test is implied by these changes.


## Layout

```text
app.yaml                 App Lab manifest
assets/                  Web UI
python/                  Host policy, Bridge client, ONNX models
sketch/                  STM32 firmware
data/zero_pos.json       Created on the robot by calibration (not in git)
tools/                   Bridge patch, router rate, ELF check, read-only capture
```

## Checks

```bash
python tools/measure.py --seconds 60
```

`tools/measure.py` only reads `http://127.0.0.1:9527/api/status`. It does not enable motors. Pass `--output` to save a JSON capture.

## References

- [Microduck](https://github.com/pollen-robotics/microduck)
- [Arduino RouterBridge](https://github.com/arduino-libraries/Arduino_RouterBridge)
- [Arduino Router](https://github.com/arduino/arduino-router)

## Combined branch and verification

The `feature/xgoduck-combined` branch combines the two Xbox frontends and the servo-voltage battery indicator. The host gamepad's exclusive lease remains authoritative: a browser gamepad will release its local input **without sending torque-off** if a native lease becomes active, so a webpage cannot hijack or unexpectedly stop the host pad. The Web **Torque off** button remains an explicit global emergency stop. No pairing or automatic reconnect arms the robot.

Browser Gamepad API tests: `node --test tests/gamepad.test.cjs`. Python tests: `python3 -m unittest discover -s tests -p 'test_*.py' -v`. The STM32 firmware and actual Bluetooth, voltage and LED operation still require physical hardware validation.
