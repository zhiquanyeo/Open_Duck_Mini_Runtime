# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Runtime for the Open Duck Mini — a small RL-controlled bipedal robot running on a Raspberry Pi Zero 2W (or Pi 5). Controls 14 Feetech STS3215 servos via USB serial using an ONNX neural network policy at 50 Hz.

## Commands

```bash
# Install (uses uv, not pip directly)
uv sync

# On Raspberry Pi 5 only (different GPIO library)
uv pip uninstall RPi.GPIO && uv pip install lgpio

# Run
uv run walk                                        # gamepad control
uv run walk --onnx_model_path /path/to/model.onnx  # custom model
uv run walk-keyboard                               # SSH keyboard control
uv run walk-watchdog                               # auto-restarts walk on crash

# Tests (hardware tests excluded by default — see pyproject.toml addopts)
uv run pytest                                      # unit tests only
uv run pytest tests/test_motor_controller.py       # requires connected robot
uv run pytest -m hardware                          # motor EEPROM config check (test_motor_config.py)

# Calibration tools
python3 tools/find_soft_offsets.py    # find joint offsets interactively
python3 tools/find_all_motor_offsets.py  # calibrate all joint offsets at once by hand
python3 tools/adjust_offsets.py       # nudge a single joint's offset live with the keyboard
python3 tools/controller_info.py      # identify gamepad axis/button indices
python3 tools/check_voltage.py        # verify servo bus voltage
python3 tools/batch_reconfigure.py    # verify/fix motor EEPROM config (PID, mode, sync-read/write settings)
```

## Architecture

Three-layer design running at 50 Hz:

**Hardware layer** (`hardware/`): `HWI` manages 14 DOFs over `/dev/ttyACM0` at 1 Mbaud using the `rustypot` library (Feetech STS3215 protocol). `rustypot` 0.1.0's Python bindings expose no voltage/temperature read, so `get_present_voltage()`/`get_present_temperature()` are cheap best-effort checks (return `None` on this build) and `HWI.read_battery_handoff()` is the real path: it briefly releases the `rustypot` connection, reads voltage+temp through `pypot.feetech.FeetechSTS3215IO` on the same port instead, then *always* reconnects `rustypot` in a `finally` (with one retry) and re-applies kp/kd gains — ~0.2s during which no goal positions are sent, so callers must only invoke it when paused/idle (see `RLWalk._sample_battery()`, throttled + paused-only). `Imu` wraps a BNO055 I2C sensor sampled in a background thread at 50 Hz and exposes cached gyro/accel/gravity values, with an optional `pitch_trim`/`roll_trim` (radians) applied via `imu_trim.py`'s pure rotation math to correct residual mounting tilt — identity at the default `0.0`. Expression peripherals (eyes, antennas, projector, speaker, camera) are all optional and gated by `expression_features` in config. Eyes/projector NeoPixel brightness is per-segment (`led_brightness.{projector,right_eye,left_eye}` in config, same shape as `led_counts`) — applied once at startup via `Eyes.set_left_eye_brightness()`/`set_right_eye_brightness()`/`Projector.set_brightness()`, which are thin wrappers over `LedController`'s already-clamped per-segment brightness API in `led_controller.py`. `Sounds.set_volume()` (config: `speaker_volume`) works the same way. Both are also live-tunable/persistable from `/control` — see `RLWalk._apply_led_brightness_settings()`/`_apply_speaker_settings()` and their `_save_*`/`_reset_*` counterparts, which track live state in `self._led_brightness`/`self._speaker_volume` (not readable back off `Eyes`/`Sounds`) so Save persists the right values even when that peripheral isn't enabled.

**Controller layer** (`controller/`): `XBoxController` polls Xbox/DualSense/generic USB gamepad or keyboard at 20 Hz in a background thread. Supports custom axis/button remapping for generic USB controllers via config. `RemoteController` is a drop-in alternative (same `.connected`/`.get_last_command()` interface) sourced from UDP packets instead of a local gamepad — used when `remote_control.enabled` is set in config, for driving the duck from a gamepad plugged into a separate PC/Steam Deck (see `remote_control/` at the repo root, a standalone PC-side package). Both controllers share axis-scaling/range-clamping logic via `command_shaping.py`. `ControlBus` (`rl_walk/control_bus.py`) is a third, independent input source — a thread-safe bus the stats server's `/api/*` routes write into and `RLWalk.run()` merges in each tick (web sticks override the active controller's only while freshly posted, buttons OR together into the same `Buttons` edge-detector, triggers are the max of both) — see `webui/control.html`, a phone-facing touch-control page.

**RL walk layer** (`rl_walk/`): `RLWalk` in `walk.py` is the main orchestrator. `OnnxInfer` wraps the ONNX policy (initialized with `awd=True`). `PolyReferenceMotion` provides a gait phase signal. `LowPassActionFilter` in `rl_utils.py` optionally smooths actions. `StabilityGovernor` (`stability_governor.py`) eases drive commands (indices 0:3 of `last_commands`, not head commands) when tipping, using the *previous* tick's cached IMU — disabled by default (pure pass-through) and sits strictly upstream of fall detection, which is a separate, unmodified layer (self-calibrating "up" vector, pause+`hwi.turn_off()` on sustained tilt). `stats_server.py`'s `StatsServer` (gated by `web_stats.enabled`) also serves `GET /telemetry`, `GET`/`POST /eye_colors`, `GET /control` (the web UI), and `POST /api/{command,button,trim,setting}` backed by `ControlBus` — telemetry is read from a cache `get_obs()` refreshes every control tick (never read live from the HTTP handler thread, since the servo bus isn't safe for concurrent access outside the control loop), and eye-color POSTs are a live-only preview via `Eyes.set_color()`, never persisted to `duck_config.json`. IMU trim and walk-tuning edits (`action_scale`, `phase_frequency_factor_offset`, `velocity_clip`, `max_motor_velocity_rad_s`, `stability_governor.*`) *are* persistable via `duck_config.save_config_fields()` (backup-first JSON patch), on an explicit Save from the web UI. Battery telemetry (`battery.py`) is sampled throttled and paused-only via `HWI.read_battery_handoff()` (never on the 50 Hz hot path).

### Control loop (50 Hz, `walk.py RLWalk.run()`)

1. Read cached IMU data (gyro, accel, gravity)
2. Read motor positions/velocities via HWI
3. Build 54-element observation: `[gyro(3), accel(3), commands(7), joint_pos(14), joint_vel(14), action_history(14×3), targets(14), foot_contacts(2), gait_phase(2)]`
4. Run ONNX inference → 14-element action
5. Motor target = `init_pos + action * action_scale` (default 0.25, ramped toward any live-tuned target by at most `ACTION_SCALE_RAMP` per tick)
6. Send targets; check fall detection (3 consecutive frames > `fall_threshold_deg`)

Between reading commands and building the observation: `ControlBus` (if active) is merged in, then `StabilityGovernor` scales `commands[0:3]` using the *previous* tick's IMU — both are no-ops on default config.

### Joint order

Defined once in `hwi.py` and must stay consistent everywhere:
```
[left_hip_yaw, left_hip_roll, left_hip_pitch, left_knee, left_ankle,
 neck_pitch, head_pitch, head_yaw, head_roll,
 right_hip_yaw, right_hip_roll, right_hip_pitch, right_knee, right_ankle]
```
`rl_utils.py` contains coordinate transforms for Mujoco ↔ Isaac Gym compatibility.

## Configuration

Loaded from `~/duck_config.json` (not in the repo). If absent, `DuckConfig` uses defaults and prompts for confirmation. See `example_config.json` for all fields. Key non-obvious fields:

- `imu_upside_down`: flips IMU orientation if mounted inverted
- `phase_frequency_factor_offset`: added to base 1.0 factor (1.3 = sprint)
- `generic_usb_controller`: override axis/button indices when `controller_type = "generic_usb"`
- `joints_offsets`: per-joint servo correction in radians (output of `find_soft_offsets.py`)
- `imu_trim` / `stability_governor` / `action_scale` / `velocity_clip` / `max_motor_velocity_rad_s`: locomotion tuning, all live-editable from the `/control` web UI (see `web_stats`) and persistable via `duck_config.save_config_fields()`

## Notes

- `uv run walk` requires a valid ONNX model — there is no bundled default. Pass `--onnx_model_path` or set it in config.
- Hardware tests in `tests/test_motor_controller.py` are excluded from the default pytest run via `addopts` in `pyproject.toml`, not by marker alone. `tests/test_motor_config.py` uses the `hardware` marker instead — it's still collected by default `uv run pytest` but self-skips (via the fixture) if `/dev/ttyACM0` can't be opened, so it shows as skipped rather than excluded.
- Assets (ONNX models, reference motions) are resolved relative to the installed package at `src/assets/`.
- The `TRACE` log level (below DEBUG) is defined in `log.py` for per-frame diagnostics.
- Every servo must have `return_delay_time=0` and `response_status_level=1` in EEPROM — nonzero delay breaks SYNC_READ, and `response_status_level=2` (respond to all commands) breaks SYNC_WRITE. This is the source-of-truth `EXPECTED` config asserted by `tests/test_motor_config.py` and applied by `tools/batch_reconfigure.py`.
- Motor batch scripts/tests use dict-based bulk operations keyed by motor ID (e.g. `{motor_id: value for motor_id in joint_ids}`) rather than per-motor loops — matches how the STS3215 protocol addresses all motors together.
- `open_duck_mini_runtime/__init__.py`, `hardware/__init__.py`, and `rl_walk/__init__.py` deliberately re-export nothing — always import submodules directly (`from open_duck_mini_runtime.hardware.hwi import HWI`, not `from open_duck_mini_runtime.hardware import HWI`). Any eager re-export in these files pulls in `raw_imu.py`'s `adafruit_bno055` import transitively, which breaks `uv run pytest` collection entirely (not just skips) on machines without the Pi-only hardware deps installed — this is exactly what happened when `test_imu_trim.py`/`test_stability_governor.py`/etc. were added and is why those `__init__.py` files were emptied out.
