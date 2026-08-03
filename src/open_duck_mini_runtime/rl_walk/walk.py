import logging
import time
import pickle

import numpy as np
from open_duck_mini_runtime.hardware.hwi import HWI
from open_duck_mini_runtime.rl_walk.onnx_infer import OnnxInfer
from open_duck_mini_runtime.hardware.raw_imu import Imu
from open_duck_mini_runtime.rl_walk.poly_reference_motion import PolyReferenceMotion
from open_duck_mini_runtime.hardware.feet_contacts import FeetContacts
from open_duck_mini_runtime.controller.xbox_controller import XBoxController
from open_duck_mini_runtime.controller.remote_controller import RemoteController
from open_duck_mini_runtime.controller.command_shaping import shape_commands
from open_duck_mini_runtime.controller.buttons import Buttons
from open_duck_mini_runtime.hardware.eyes import Eyes
from open_duck_mini_runtime.hardware.sounds import Sounds
from open_duck_mini_runtime.hardware.antennas import Antennas
from open_duck_mini_runtime.hardware.projector import Projector
from open_duck_mini_runtime.rl_walk.rl_utils import (
    make_action_dict,
    LowPassActionFilter,
)
from open_duck_mini_runtime.rl_walk.stats_server import StatsServer
from open_duck_mini_runtime.rl_walk.control_bus import ControlBus
from open_duck_mini_runtime.rl_walk.stability_governor import (
    governor_from_config,
    tilt_angle_deg,
    tilt_rate,
    accel_pitch_roll,
)
from open_duck_mini_runtime.rl_walk.battery import ChargeEstimator, estimate_percent
from open_duck_mini_runtime.rl_walk.walk_defaults import (
    WALK_TUNING_DEFAULTS,
    IMU_TRIM_DEFAULTS,
    LED_BRIGHTNESS_DEFAULTS,
    SPEAKER_DEFAULTS,
)
from open_duck_mini_runtime.duck_config import DuckConfig, save_config_fields
from open_duck_mini_runtime.log import setup_logging, TRACE

import os
import signal
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

HOME_DIR = os.path.expanduser("~")
ASSETS_ROOT_PATH: str = str(Path(__file__).parent.parent / "assets")

# action_scale is ramped toward a web-set target so a live change can't step the
# leg amplitude in one tick (motor safety). Max change per control tick:
ACTION_SCALE_RAMP = 0.02

# Live IMU-trim tuner (web control UI): hard clamp so a stuck input can't drive
# the trim to a dangerous angle.
TRIM_LIMIT = 0.1  # rad (~5.7 deg) max |trim| on either axis

# How often (s) to sample the battery — throttled and paused-only, see run().
BATTERY_SAMPLE_PERIOD_S = 5.0

# Buttons ControlBus (the web control UI) can supply — matches ControlBus.BUTTONS.
# start/back/LStickButton/RStickButton aren't in this set since ControlBus has no
# equivalent web control for them; the pad's own state for those passes through
# untouched.
_CONTROL_BUS_MERGE_BUTTONS = (
    "A", "B", "X", "Y", "LB", "RB", "dpad_up", "dpad_down", "dpad_left", "dpad_right",
)


class RLWalk:
    def __init__(
        self,
        onnx_model_path: str,
        duck_config_path: str = f"{HOME_DIR}/duck_config.json",
        serial_port: str = "/dev/ttyACM0",
        control_freq: float = 50,
        pid=[30, 0, 0],
        action_scale=0.25,
        commands=False,
        pitch_bias=0,
        save_obs=False,
        replay_obs=None,
        cutoff_frequency=None,
        head_only=False,
    ):

        self.duck_config_path = duck_config_path
        self.duck_config = DuckConfig(config_json_path=duck_config_path)

        self.commands = commands
        self.head_only = head_only
        self.pitch_bias = pitch_bias

        self.onnx_model_path = onnx_model_path
        self.policy = OnnxInfer(self.onnx_model_path, awd=True)

        self.num_dofs = 14
        # Live-tunable via the web control UI; velocity_clip is exposed/persisted
        # for parity but not enforced yet (see max_motor_velocity's only other use
        # in run(), which stays commented out — a separate follow-up).
        self.max_motor_velocity = self.duck_config.max_motor_velocity_rad_s  # rad/s
        self.velocity_clip = self.duck_config.velocity_clip

        # Control
        self.control_freq = control_freq
        self.pid = pid

        self.save_obs = save_obs
        if self.save_obs:
            self.saved_obs = []

        self.replay_obs = replay_obs
        if self.replay_obs is not None:
            self.replay_obs = pickle.load(open(self.replay_obs, "rb"))

        self.action_filter = None
        if cutoff_frequency is not None:
            self.action_filter = LowPassActionFilter(
                self.control_freq, cutoff_frequency
            )

        self.hwi = HWI(self.duck_config, serial_port)

        self.start()

        self.imu = Imu(
            sampling_freq=int(self.control_freq),
            user_pitch_bias=self.pitch_bias,
            upside_down=self.duck_config.imu_upside_down,
            pitch_trim=self.duck_config.imu_trim["pitch"],
            roll_trim=self.duck_config.imu_trim["roll"],
        )

        self.feet_contacts = FeetContacts()

        # Scales — config overrides the CLI default when explicitly set.
        # action_scale ramps toward _action_scale_target (see run()) so a live
        # web-tuning edit can't step the leg amplitude in one tick.
        self.action_scale = (
            self.duck_config.action_scale
            if self.duck_config.action_scale is not None
            else action_scale
        )
        self._action_scale_target = self.action_scale

        self.last_action = np.zeros(self.num_dofs)
        self.last_last_action = np.zeros(self.num_dofs)
        self.last_last_last_action = np.zeros(self.num_dofs)

        self.init_pos = list(self.hwi.init_pos.values())

        self.motor_targets = np.array(self.init_pos.copy())
        self.prev_motor_targets = np.array(self.init_pos.copy())

        self.last_commands = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        self.paused = self.duck_config.start_paused
        self.motors_enabled = True

        # Latest sensor snapshot, refreshed each control tick in get_obs() and
        # exposed read-only via get_telemetry() (e.g. to the stats server) —
        # never read live from a second thread, since the servo bus isn't
        # safe for concurrent access from outside the control loop.
        self._telemetry_dof_pos = None
        self._telemetry_imu_data = None
        self._telemetry_feet_contacts = None

        # Fall detection: calibrate "up" from gravity samples collected while paused
        self._up_vector: np.ndarray | None = None
        self._up_calib_acc = np.zeros(3)
        self._up_calib_count = 0
        self._up_calib_target = 10  # 10 samples × 0.1 s pause loop = ~1 s
        self._fall_consecutive = 0
        self._fall_consecutive_required = 3  # frames at 50 Hz before triggering

        # Stability governor: an additional, independent, disabled-by-default
        # layer that eases drive commands when tipping — sits strictly upstream
        # of fall detection above, which is untouched and still the safety net
        # that pauses + turns off motors.
        self.governor = governor_from_config(self.duck_config.stability_governor)

        # Web control UI (phone browser, no PC app needed) — an alternate input
        # source merged into the gamepad/remote-controller command each tick in
        # run(), only constructed when the stats server is actually serving it.
        self.control_bus = (
            ControlBus() if self.duck_config.web_stats_enabled else None
        )
        # Independent edge-detector state for web-sourced button presses —
        # deliberately a SEPARATE Buttons() instance rather than calling
        # self.buttons.update() a second time per tick: Button.update() is a
        # stateful edge-detector meant for exactly one call per tick, and
        # calling it twice on the same object (once from the pad, once for
        # the web merge) makes the second call see "still held from before"
        # and immediately clear .triggered right after the first call set it
        # — which silently broke every tap-triggered action (pause/resume,
        # sound, projector, gait-offset nudges) whenever web_stats was
        # enabled, whether or not the web UI was actually in use. Each
        # instance gets exactly one update() call; results are OR'd together
        # as plain attribute writes afterward, which doesn't touch either
        # object's internal debounce state.
        self._control_bus_buttons = Buttons()
        self._prev_control_bus_Y = False

        # Battery: throttled, paused-only sampling via HWI.read_battery_handoff()
        # — see run()'s paused branch and _sample_battery(). estimate_percent/
        # ChargeEstimator both handle a None voltage (bus read failure) gracefully.
        battery_kwargs = {}
        if "v_full" in self.duck_config.battery:
            battery_kwargs["v_full"] = self.duck_config.battery["v_full"]
        self._battery_estimator = ChargeEstimator(**battery_kwargs)
        self._telemetry_battery = {"voltage": None, "percent": None, "charging": None}
        self._battery_last_sample_t = 0.0

        self.command_freq = 20  # hz
        if self.commands:
            if self.duck_config.remote_control_enabled:
                self.controller = RemoteController(
                    self.command_freq,
                    port=self.duck_config.remote_control_port,
                    only_head_control=head_only,
                )
            else:
                self.controller = XBoxController(
                    self.command_freq, only_head_control=head_only
                )

        # Reference motion, but we only really need the length of one phase
        self.PRM = PolyReferenceMotion(
            f"{ASSETS_ROOT_PATH}/polynomial_coefficients.pkl"
        )
        self.imitation_i = 0
        self.imitation_phase = np.array([0, 0])
        self.phase_frequency_factor = 1.0
        self.phase_frequency_factor_offset = (
            self.duck_config.phase_frequency_factor_offset
        )

        # Live per-segment LED brightness — starts from config, but is the
        # authoritative "current value" from here on (live-tuned via the web
        # control UI's /api/setting "led_brightness" group; see
        # _apply_led_brightness_settings()/_save_led_brightness_settings()).
        self._led_brightness = dict(self.duck_config.led_brightness)

        # Same pattern for speaker volume — see _apply_speaker_settings() /
        # _save_speaker_settings() ("speaker" settings group).
        self._speaker_volume = self.duck_config.speaker_volume

        # Optional expression features
        if self.duck_config.eyes:
            self.eyes = Eyes(
                neopixels=self.duck_config.neopixels,
                led_counts=self.duck_config.led_counts,
            )
            self.eyes.set_left_eye_brightness(self._led_brightness["left_eye"])
            self.eyes.set_right_eye_brightness(self._led_brightness["right_eye"])
            if self.paused:
                self.eyes.set_standby(True)
                self.eyes.set_solid(False)
                self.eyes.set_color(self._ec(self.duck_config.eye_color_paused))
            else:
                self.eyes.set_standby(False)
                self.eyes.set_solid(False)
                self.eyes.set_color(self._ec(self.duck_config.eye_color_start))
        if self.duck_config.projector:
            self.projector = Projector(led_counts=self.duck_config.led_counts)
            self.projector.set_brightness(self._led_brightness["projector"])
        if self.duck_config.speaker:
            self.sounds = Sounds(
                volume=self._speaker_volume, sound_directory=ASSETS_ROOT_PATH
            )
        if self.duck_config.antennas:
            self.antennas = Antennas()

        self.stats_server = None
        if self.duck_config.web_stats_enabled:
            self.stats_server = StatsServer(
                self.get_stats,
                port=self.duck_config.web_stats_port,
                get_telemetry=self.get_telemetry,
                get_eye_colors=self.get_eye_colors,
                set_eye_colors=self.set_eye_color_preview,
                control_bus=self.control_bus,
            )

    @staticmethod
    def _ec(color):
        """Normalize an eye color from config (list or string) to what Eyes.set_color accepts."""
        return tuple(color) if isinstance(color, list) else color

    def get_stats(self) -> dict:
        resultant_factor = self.phase_frequency_factor + self.phase_frequency_factor_offset
        gait_hz = (
            self.control_freq * resultant_factor / self.PRM.nb_steps_in_period
        )
        return {
            "mode": "head_puppet" if self.head_only else "walk",
            "paused": self.paused,
            "motors_enabled": self.motors_enabled,
            "gamepad_connected": (
                self.controller.connected if self.commands else False
            ),
            "phase_frequency_factor": round(self.phase_frequency_factor, 3),
            "phase_frequency_factor_offset": round(
                self.phase_frequency_factor_offset, 3
            ),
            "resultant_frequency_factor": round(resultant_factor, 3),
            "gait_frequency_hz": round(gait_hz, 3),
            "control_freq_hz": self.control_freq,
            "walk_tuning": {
                "action_scale": round(float(self._action_scale_target), 4),
                "velocity_clip": bool(self.velocity_clip),
                "max_motor_velocity_rad_s": round(float(self.max_motor_velocity), 3),
            },
            "stability_governor": self._governor_config_dict(),
            "imu_trim": {
                "pitch": round(float(self.imu.pitch_trim), 5),
                "roll": round(float(self.imu.roll_trim), 5),
            },
            "led_brightness": {k: round(v, 4) for k, v in self._led_brightness.items()},
            "speaker": {"volume": round(float(self._speaker_volume), 4)},
        }

    def get_telemetry(self) -> dict:
        """Read-only sensor snapshot for the stats server's /telemetry endpoint.
        Reads the cache get_obs() refreshes every control tick — never touches
        the servo bus directly, since that's only safe from the control loop."""
        joint_names = list(self.hwi.joints.keys())
        dof_pos = self._telemetry_dof_pos
        imu_data = self._telemetry_imu_data

        return {
            "timestamp": time.time(),
            "paused": self.paused,
            "motors_enabled": self.motors_enabled,
            "joint_positions": (
                dict(zip(joint_names, np.round(dof_pos, 4).tolist()))
                if dof_pos is not None
                else None
            ),
            "motor_targets": dict(
                zip(joint_names, np.round(np.asarray(self.motor_targets), 4).tolist())
            ),
            "imu": (
                {
                    "gyro": np.round(imu_data["gyro"], 4).tolist(),
                    "accel": np.round(imu_data["accelero"], 4).tolist(),
                    "gravity": np.round(imu_data["gravity"], 4).tolist(),
                }
                if imu_data is not None
                else None
            ),
            "feet_contacts": self._telemetry_feet_contacts,
            "battery": self._telemetry_battery,
        }

    def get_eye_colors(self) -> dict:
        return {
            "enabled": self.duck_config.eyes,
            "neopixels": self.duck_config.neopixels,
            "start": list(self.duck_config.eye_color_start),
            "paused": list(self.duck_config.eye_color_paused),
            "off": list(self.duck_config.eye_color_off),
        }

    def set_eye_color_preview(self, data: dict) -> dict:
        """Live-only eye color override for the stats server's POST
        /eye_colors endpoint — lets a PC-side tool try colors on the real
        hardware. Not persisted to duck_config.json; a restart (or another
        pause/unpause transition) reverts to the configured colors."""
        if not self.duck_config.eyes:
            raise ValueError("eyes expression feature is not enabled in duck_config.json")

        if data.get("clear"):
            self.eyes.set_solid(False)
            return {"preview": None}

        color = data.get("color")
        if not (isinstance(color, list) and len(color) == 3):
            raise ValueError("expected {'color': [r, g, b]} or {'clear': true}")

        self.eyes.set_solid(True)
        self.eyes.set_color(self._ec(color))
        return {"preview": color}

    def _consume_control_bus_settings(self):
        """Drain and apply the web control UI's live-settings edits + save/reset
        requests. Cheap: one lock, then plain attribute writes (no hardware in the
        hot path)."""
        settings, saves, resets = self.control_bus.consume_settings()
        if settings.get("walk"):
            self._apply_walk_settings(settings["walk"])
        if "walk" in resets:
            self._reset_walk_settings()
        if "walk" in saves:
            self._save_walk_settings()

        if settings.get("led_brightness"):
            self._apply_led_brightness_settings(settings["led_brightness"])
        if "led_brightness" in resets:
            self._reset_led_brightness_settings()
        if "led_brightness" in saves:
            self._save_led_brightness_settings()

        if settings.get("speaker"):
            self._apply_speaker_settings(settings["speaker"])
        if "speaker" in resets:
            self._reset_speaker_settings()
        if "speaker" in saves:
            self._save_speaker_settings()

        pitch_delta, roll_delta, save_trim = self.control_bus.consume_trim()
        if pitch_delta or roll_delta:
            self.imu.pitch_trim = float(
                np.clip(self.imu.pitch_trim + pitch_delta, -TRIM_LIMIT, TRIM_LIMIT)
            )
            self.imu.roll_trim = float(
                np.clip(self.imu.roll_trim + roll_delta, -TRIM_LIMIT, TRIM_LIMIT)
            )
        if "imu_trim" in resets:
            self._reset_imu_trim()
        if save_trim:
            self._save_imu_trim()

    def _apply_walk_settings(self, d):
        """Apply live walk-tuning edits. action_scale is ramped (see run()), not
        stepped, so a live change can't jump the leg amplitude in one tick."""
        for key, v in d.items():
            try:
                if key == "action_scale":
                    self._action_scale_target = float(np.clip(float(v), 0.0, 0.6))
                elif key == "phase_frequency_factor_offset":
                    self.phase_frequency_factor_offset = float(
                        np.clip(float(v), -0.5, 0.5)
                    )
                elif key == "velocity_clip":
                    self.velocity_clip = bool(v)
                elif key == "max_motor_velocity_rad_s":
                    self.max_motor_velocity = float(np.clip(float(v), 0.5, 12.0))
                elif key == "governor_enabled":
                    self.governor.enabled = bool(v)
                elif key == "governor_tilt_lo_deg":
                    self.governor.tilt_lo_deg = float(np.clip(float(v), 0.0, 45.0))
                elif key == "governor_tilt_hi_deg":
                    self.governor.tilt_hi_deg = float(np.clip(float(v), 0.0, 60.0))
                elif key == "governor_rate_lo":
                    self.governor.rate_lo = float(np.clip(float(v), 0.0, 20.0))
                elif key == "governor_rate_hi":
                    self.governor.rate_hi = float(np.clip(float(v), 0.0, 30.0))
                elif key == "governor_floor":
                    self.governor.floor = float(np.clip(float(v), 0.0, 1.0))
                elif key == "governor_smooth":
                    self.governor.smooth = float(np.clip(float(v), 0.01, 1.0))
            except (ValueError, TypeError):
                logger.warning("Ignoring bad walk setting %s=%r", key, v)

    def _governor_config_dict(self) -> dict:
        g = self.governor
        return {
            "enabled": bool(g.enabled),
            "tilt_lo_deg": round(float(g.tilt_lo_deg), 3),
            "tilt_hi_deg": round(float(g.tilt_hi_deg), 3),
            "rate_lo": round(float(g.rate_lo), 3),
            "rate_hi": round(float(g.rate_hi), 3),
            "floor": round(float(g.floor), 3),
            "smooth": round(float(g.smooth), 3),
        }

    def _save_walk_settings(self):
        fields = {
            "action_scale": round(float(self._action_scale_target), 4),
            "phase_frequency_factor_offset": round(
                float(self.phase_frequency_factor_offset), 4
            ),
            "velocity_clip": bool(self.velocity_clip),
            "max_motor_velocity_rad_s": round(float(self.max_motor_velocity), 3),
            "stability_governor": self._governor_config_dict(),
        }
        backup = save_config_fields(fields, config_json_path=self.duck_config_path)
        logger.info("Saved walk tuning %s (backup %s)", fields, backup)

    def _reset_walk_settings(self):
        """Live-reset walk tuning to the known-good defaults (NOT persisted until
        the next Save)."""
        d = WALK_TUNING_DEFAULTS
        self._action_scale_target = float(d["action_scale"])
        self.phase_frequency_factor_offset = float(d["phase_frequency_factor_offset"])
        self.velocity_clip = bool(d["velocity_clip"])
        self.max_motor_velocity = float(d["max_motor_velocity_rad_s"])
        g = d["stability_governor"]
        self.governor.enabled = bool(g["enabled"])
        self.governor.tilt_lo_deg = float(g["tilt_lo_deg"])
        self.governor.tilt_hi_deg = float(g["tilt_hi_deg"])
        self.governor.rate_lo = float(g["rate_lo"])
        self.governor.rate_hi = float(g["rate_hi"])
        self.governor.floor = float(g["floor"])
        self.governor.smooth = float(g["smooth"])
        logger.info("Reset walk tuning to defaults %s", d)

    def _apply_led_brightness_settings(self, d):
        """Apply live per-segment brightness edits. No-ops for a segment whose
        feature isn't enabled (self.eyes/self.projector don't exist then) —
        the value is still tracked in _led_brightness so Save persists it."""
        for key, v in d.items():
            if key not in self._led_brightness:
                logger.warning("Ignoring unknown led_brightness key %r", key)
                continue
            try:
                value = float(np.clip(float(v), 0.0, 1.0))
            except (ValueError, TypeError):
                logger.warning("Ignoring bad led_brightness setting %s=%r", key, v)
                continue
            self._led_brightness[key] = value
            if key == "projector" and self.duck_config.projector:
                self.projector.set_brightness(value)
            elif key == "left_eye" and self.duck_config.eyes:
                self.eyes.set_left_eye_brightness(value)
            elif key == "right_eye" and self.duck_config.eyes:
                self.eyes.set_right_eye_brightness(value)

    def _save_led_brightness_settings(self):
        fields = {"led_brightness": {k: round(v, 4) for k, v in self._led_brightness.items()}}
        backup = save_config_fields(fields, config_json_path=self.duck_config_path)
        logger.info("Saved led_brightness %s (backup %s)", fields, backup)

    def _reset_led_brightness_settings(self):
        """Live-reset LED brightness to full (NOT persisted until the next Save)."""
        self._apply_led_brightness_settings(LED_BRIGHTNESS_DEFAULTS)
        logger.info("Reset led_brightness to defaults %s", LED_BRIGHTNESS_DEFAULTS)

    def _apply_speaker_settings(self, d):
        """Apply live speaker-volume edits. No-op on the hardware if speaker
        isn't enabled (self.sounds doesn't exist then) — the value is still
        tracked in _speaker_volume so Save persists it."""
        if "volume" not in d:
            return
        try:
            value = float(np.clip(float(d["volume"]), 0.0, 1.0))
        except (ValueError, TypeError):
            logger.warning("Ignoring bad speaker setting volume=%r", d["volume"])
            return
        self._speaker_volume = value
        if self.duck_config.speaker:
            self.sounds.set_volume(value)

    def _save_speaker_settings(self):
        fields = {"speaker_volume": round(float(self._speaker_volume), 4)}
        backup = save_config_fields(fields, config_json_path=self.duck_config_path)
        logger.info("Saved speaker_volume %s (backup %s)", fields, backup)

    def _reset_speaker_settings(self):
        """Live-reset speaker volume to full (NOT persisted until the next Save)."""
        self._apply_speaker_settings(SPEAKER_DEFAULTS)
        logger.info("Reset speaker volume to defaults %s", SPEAKER_DEFAULTS)

    def _save_imu_trim(self):
        trim = {"pitch": float(self.imu.pitch_trim), "roll": float(self.imu.roll_trim)}
        backup = save_config_fields(
            {"imu_trim": trim}, config_json_path=self.duck_config_path
        )
        logger.info("Saved imu_trim=%s (backup %s)", trim, backup)

    def _reset_imu_trim(self):
        """Live-reset the IMU mounting trim to neutral (NOT persisted until Save)."""
        self.imu.pitch_trim = float(IMU_TRIM_DEFAULTS["pitch"])
        self.imu.roll_trim = float(IMU_TRIM_DEFAULTS["roll"])
        logger.info("Reset imu trim to neutral")

    def get_obs(self):

        imu_data = self.imu.get_data()
        self._telemetry_imu_data = imu_data

        dof_pos = self.hwi.get_present_positions(
            ignore=[
                "left_antenna",
                "right_antenna",
            ]
        )  # rad

        dof_vel = self.hwi.get_present_velocities(
            ignore=[
                "left_antenna",
                "right_antenna",
            ]
        )  # rad/s

        self._telemetry_dof_pos = dof_pos

        if dof_pos is None or dof_vel is None:
            return None

        if len(dof_pos) != self.num_dofs:
            logger.warning("dof_pos length %d != %d", len(dof_pos), self.num_dofs)
            return None

        if len(dof_vel) != self.num_dofs:
            logger.warning("dof_vel length %d != %d", len(dof_vel), self.num_dofs)
            return None

        cmds = self.last_commands

        feet_contacts = self.feet_contacts.get()
        self._telemetry_feet_contacts = feet_contacts

        obs = np.concatenate(
            [
                imu_data["gyro"],
                imu_data["accelero"],
                cmds,
                dof_pos - self.init_pos,
                dof_vel * 0.05,
                self.last_action,
                self.last_last_action,
                self.last_last_last_action,
                self.motor_targets,
                feet_contacts,
                self.imitation_phase,
            ]
        )

        return obs

    def start(self):
        kps = [self.pid[0]] * 14
        kds = [self.pid[2]] * 14

        # lower head kps
        kps[5:9] = [8, 8, 8, 8]

        self.hwi.set_kps(kps)
        self.hwi.set_kds(kds)
        self.hwi.turn_on()

        time.sleep(2)

    def get_phase_frequency_factor(self, x_velocity):

        max_phase_frequency = 1.2
        min_phase_frequency = 1.0

        # Perform linear interpolation
        freq = min_phase_frequency + (abs(x_velocity) / 0.15) * (
            max_phase_frequency - min_phase_frequency
        )

        return freq

    def _reset_fall_calibration(self):
        self._up_vector = None
        self._up_calib_acc = np.zeros(3)
        self._up_calib_count = 0
        self._fall_consecutive = 0

    def _update_fall_calibration(self):
        """Accumulate gravity samples while paused to establish the upright reference."""
        imu_data = self.imu.get_data()
        g = np.asarray(imu_data.get("gravity", [0, 0, 0]), dtype=float)
        g_norm = np.linalg.norm(g)
        if g_norm < 0.5:
            return
        self._up_calib_acc += g / g_norm
        self._up_calib_count += 1
        if self._up_calib_count >= self._up_calib_target:
            up = self._up_calib_acc / self._up_calib_count
            self._up_vector = up / np.linalg.norm(up)
            logger.info(
                "Fall detection calibrated (up=%s)", np.around(self._up_vector, 3)
            )
            # Reset so we keep refreshing the reference each subsequent pause
            self._up_calib_acc = np.zeros(3)
            self._up_calib_count = 0

    def _sample_battery(self):
        """Throttled, paused-only battery sample (see run()'s paused branch —
        never called while actively walking). Uses HWI.read_battery_handoff(),
        which briefly releases and reconnects the servo bus (~0.2s, no goal
        positions sent during that window) — exactly why this is gated to the
        paused branch and throttled to BATTERY_SAMPLE_PERIOD_S, never called
        from the hot 50 Hz path."""
        now = time.time()
        if now - self._battery_last_sample_t < BATTERY_SAMPLE_PERIOD_S:
            return
        self._battery_last_sample_t = now
        voltage, _temp = self.hwi.read_battery_handoff()
        v_min = self.duck_config.battery.get("v_min")
        v_max = self.duck_config.battery.get("v_max")
        kwargs = {}
        if v_min is not None:
            kwargs["v_min"] = v_min
        if v_max is not None:
            kwargs["v_max"] = v_max
        self._telemetry_battery = {
            "voltage": voltage,
            "percent": estimate_percent(voltage, **kwargs),
            "charging": self._battery_estimator.update(now, voltage),
        }

    def _fall_detected(self):
        if self._up_vector is None:
            return False
        imu_data = self.imu.get_data()
        g = np.asarray(imu_data.get("gravity", [0, 0, 0]), dtype=float)
        if not np.all(np.isfinite(g)):
            self._fall_consecutive = 0
            return False
        g_norm = np.linalg.norm(g)
        if g_norm < 0.5:
            self._fall_consecutive = 0
            return False
        cos_angle = np.clip(abs(np.dot(g / g_norm, self._up_vector)), 0.0, 1.0)
        tilt_deg = np.degrees(np.arccos(cos_angle))
        if tilt_deg > self.duck_config.fall_threshold_deg:
            self._fall_consecutive += 1
            if self._fall_consecutive >= self._fall_consecutive_required:
                axis_labels = ["X", "Y", "Z"]
                worst = axis_labels[
                    int(np.argmax(np.abs(g / g_norm - self._up_vector)))
                ]
                logger.warning(
                    "tilt=%.1f° (%s-axis dominant) gravity=%s",
                    tilt_deg,
                    worst,
                    np.around(g, 3),
                )
                return True
        else:
            self._fall_consecutive = 0
        return False

    def run(self):
        """Returns True if the caller should relaunch into mode selection
        (LB+RB+B pressed while paused), False on a normal shutdown."""
        signal.signal(
            signal.SIGTERM, lambda s, f: (_ for _ in ()).throw(KeyboardInterrupt())
        )

        i = 0
        restart_requested = False
        try:
            logger.info("Starting main loop")
            start_t = time.time()
            while True:
                left_trigger = 0
                right_trigger = 0
                t = time.time()

                if self.commands:
                    self.last_commands, self.buttons, left_trigger, right_trigger = (
                        self.controller.get_last_command()
                    )

                    if self.control_bus is not None:
                        # Web sticks OVERRIDE the pad's while actively posted
                        # (stale -> pad wins); triggers are the max of both.
                        active, cb_l_x, cb_l_y, cb_r_x, cb_r_y, cb_lt, cb_rt = (
                            self.control_bus.stick_override(t)
                        )
                        if active:
                            self.last_commands = shape_commands(
                                cb_l_x,
                                cb_l_y,
                                cb_r_x,
                                self.controller.head_control_mode,
                                self.last_commands,
                            )
                        left_trigger = max(left_trigger, cb_lt)
                        right_trigger = max(right_trigger, cb_rt)

                        # Web Y toggles head-control mode independently of the
                        # pad's own Y edge-detection (which already ran inside
                        # get_last_command() above) — tracked separately so a
                        # physical pad Y-press can't get double-counted here.
                        cb_buttons = self.control_bus.consume_buttons()
                        cb_Y = cb_buttons["Y"]
                        if (
                            cb_Y
                            and not self._prev_control_bus_Y
                            and not self.controller.only_head_control
                        ):
                            self.controller.head_control_mode = (
                                not self.controller.head_control_mode
                            )
                        self._prev_control_bus_Y = cb_Y

                        # Feed the web buttons through their OWN edge-detector
                        # (exactly one update() call, same as the pad's
                        # self.buttons above) — see _control_bus_buttons'
                        # definition in __init__ for why this can't be a
                        # second update() call on self.buttons itself.
                        self._control_bus_buttons.update(
                            cb_buttons["A"],
                            cb_buttons["B"],
                            cb_buttons["X"],
                            cb_Y,
                            cb_buttons["LB"],
                            cb_buttons["RB"],
                            cb_buttons["dpad_up"],
                            cb_buttons["dpad_down"],
                            dpad_left=cb_buttons["dpad_left"],
                            dpad_right=cb_buttons["dpad_right"],
                        )
                        # OR the two independently-computed results together —
                        # plain attribute writes, not another update() call, so
                        # neither tracker's debounce state is disturbed.
                        for name in _CONTROL_BUS_MERGE_BUTTONS:
                            pad_btn = getattr(self.buttons, name)
                            web_btn = getattr(self._control_bus_buttons, name)
                            pad_btn.is_pressed = pad_btn.is_pressed or web_btn.is_pressed
                            pad_btn.triggered = pad_btn.triggered or web_btn.triggered

                        self._consume_control_bus_settings()

                    # Stability governor: ease drive commands (indices 0:3 only;
                    # head commands untouched) using the PREVIOUS tick's cached
                    # IMU — this tick's fresh read happens below in get_obs().
                    # Disabled by default -> pure pass-through (scale == 1.0),
                    # and sits strictly upstream of fall detection, which is
                    # unchanged and remains the pause+motors-off safety net.
                    if self._telemetry_imu_data is not None:
                        pr = accel_pitch_roll(self._telemetry_imu_data.get("accelero"))
                        if pr is not None:
                            tilt_deg = tilt_angle_deg(pr["pitch"], pr["roll"])
                            rate = tilt_rate(
                                self._telemetry_imu_data.get("gyro", [0.0, 0.0])
                            )
                            scale = self.governor.update(tilt_deg, rate)
                            if scale != 1.0:
                                for k in range(3):
                                    self.last_commands[k] *= scale

                    if (
                        self.paused
                        and self.buttons.LB.is_pressed
                        and self.buttons.RB.is_pressed
                        and self.buttons.B.triggered
                    ):
                        logger.info(
                            "LB+RB+B pressed while paused — exiting to mode selection"
                        )
                        restart_requested = True
                        break

                    if self.buttons.dpad_up.triggered:
                        self.phase_frequency_factor_offset += 0.05
                        logger.info(
                            "Phase frequency factor offset: %.3f",
                            self.phase_frequency_factor_offset,
                        )

                    if self.buttons.dpad_down.triggered:
                        self.phase_frequency_factor_offset -= 0.05
                        logger.info(
                            "Phase frequency factor offset: %.3f",
                            self.phase_frequency_factor_offset,
                        )

                    if self.buttons.LB.is_pressed:
                        self.phase_frequency_factor = 1.3
                    else:
                        self.phase_frequency_factor = 1.0

                    if self.buttons.X.triggered:
                        if self.duck_config.projector:
                            self.projector.switch()

                    if self.buttons.B.triggered:
                        if self.duck_config.speaker:
                            self.sounds.play_random_sound()

                    if self.duck_config.antennas:
                        self.antennas.set_position_left(right_trigger)
                        self.antennas.set_position_right(left_trigger)

                    if self.buttons.A.triggered:
                        if not self.motors_enabled:
                            logger.info(
                                "Motors are off – press START to re-enable first"
                            )
                        else:
                            self.paused = not self.paused
                            if self.paused:
                                logger.info("PAUSE")
                                if self.duck_config.eyes:
                                    self.eyes.set_standby(True)
                                    self.eyes.set_solid(False)
                                    self.eyes.set_color(
                                        self._ec(self.duck_config.eye_color_paused)
                                    )
                            else:
                                self._fall_consecutive = 0
                                logger.info("UNPAUSE")
                                if self.duck_config.eyes:
                                    self.eyes.set_standby(False)
                                    self.eyes.set_solid(False)
                                    self.eyes.set_color(
                                        self._ec(self.duck_config.eye_color_start)
                                    )

                    if self.buttons.START.triggered:
                        if self.motors_enabled:
                            logger.info("START – turning motors OFF")
                            self.hwi.turn_off()
                            self.motors_enabled = False
                            self.paused = True
                            if self.duck_config.eyes:
                                self.eyes.set_standby(False)
                                self.eyes.set_solid(True)
                                self.eyes.set_color(
                                    self._ec(self.duck_config.eye_color_off)
                                )
                        else:
                            logger.info("START – turning motors ON and reinitialising")
                            self.start()
                            self.motors_enabled = True
                            self.paused = True  # start paused; press A to begin walking
                            start_t = time.time()  # reset action-filter warmup timer
                            if self.duck_config.eyes:
                                self.eyes.set_standby(True)
                                self.eyes.set_solid(False)
                                self.eyes.set_color(
                                    self._ec(self.duck_config.eye_color_paused)
                                )

                # Fall detection — only while actively walking (motors on, not paused)
                if (
                    self.duck_config.fall_detection
                    and self.motors_enabled
                    and not self.paused
                    and self._fall_detected()
                ):
                    logger.warning(
                        "FALL DETECTED (tilt > %d°) – turning off motors",
                        self.duck_config.fall_threshold_deg,
                    )
                    self.hwi.turn_off()
                    self.motors_enabled = False
                    self.paused = True
                    if self.duck_config.eyes:
                        self.eyes.set_standby(False)
                        self.eyes.set_solid(True)
                        self.eyes.set_color(self._ec(self.duck_config.eye_color_off))

                if self.paused:
                    if self.motors_enabled and self.duck_config.fall_detection:
                        self._update_fall_calibration()
                    self._sample_battery()
                    time.sleep(0.1)
                    continue

                obs = self.get_obs()
                if obs is None:
                    continue
                logger.trace("obs: %s", np.around(obs, 3))

                self.imitation_i += 1 * (
                    self.phase_frequency_factor + self.phase_frequency_factor_offset
                )
                self.imitation_i = self.imitation_i % self.PRM.nb_steps_in_period
                self.imitation_phase = np.array(
                    [
                        np.cos(
                            self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
                        ),
                        np.sin(
                            self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
                        ),
                    ]
                )

                if self.save_obs:
                    self.saved_obs.append(obs)

                if self.replay_obs is not None:
                    if i < len(self.replay_obs):
                        obs = self.replay_obs[i]
                    else:
                        logger.info("Replay observations exhausted, stopping")
                        break

                action = self.policy.infer(obs)
                logger.trace("action: %s", np.around(action, 3))

                self.last_last_last_action = self.last_last_action.copy()
                self.last_last_action = self.last_action.copy()
                self.last_action = action.copy()

                # action = np.zeros(10)

                # Ramp toward any live-tuned target rather than stepping instantly.
                if self.action_scale != self._action_scale_target:
                    step = np.clip(
                        self._action_scale_target - self.action_scale,
                        -ACTION_SCALE_RAMP,
                        ACTION_SCALE_RAMP,
                    )
                    self.action_scale += step

                self.motor_targets = self.init_pos + action * self.action_scale

                # self.motor_targets = np.clip(
                #     self.motor_targets,
                #     self.prev_motor_targets
                #     - self.max_motor_velocity * (1 / self.control_freq),  # control dt
                #     self.prev_motor_targets
                #     + self.max_motor_velocity * (1 / self.control_freq),  # control dt
                # )

                if self.action_filter is not None:
                    self.action_filter.push(self.motor_targets)
                    filtered_motor_targets = self.action_filter.get_filtered_action()
                    if (
                        time.time() - start_t > 1
                    ):  # give time to the filter to stabilize
                        self.motor_targets = filtered_motor_targets

                self.prev_motor_targets = self.motor_targets.copy()

                head_motor_targets = self.last_commands[3:] + self.motor_targets[5:9]
                self.motor_targets[5:9] = head_motor_targets

                action_dict = make_action_dict(
                    self.motor_targets, list(self.hwi.joints.keys())
                )

                logger.trace("motor targets: %s", np.around(self.motor_targets, 3))
                self.hwi.set_position_all(action_dict)

                i += 1

                took = time.time() - t
                logger.trace(
                    "Loop %d: %.4fs (%.1f Hz)", i, took, 1 / took if took else 0
                )
                if (1 / self.control_freq - took) < 0:
                    logger.debug(
                        "Control budget exceeded by %.3fs", took - 1 / self.control_freq
                    )
                time.sleep(max(0, 1 / self.control_freq - took))

        except KeyboardInterrupt:
            pass
        finally:
            if self.stats_server is not None:
                self.stats_server.stop()
            if self.duck_config.antennas:
                self.antennas.stop()
            if self.duck_config.eyes:
                self.eyes.stop()
            if self.duck_config.projector:
                self.projector.stop()
            self.feet_contacts.stop()
            logger.info("Turning off motors")
            self.hwi.turn_off()

        if self.save_obs:
            pickle.dump(self.saved_obs, open("robot_saved_obs.pkl", "wb"))

        return restart_requested


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--onnx_model_path",
        type=str,
        required=False,
        default=f"{HOME_DIR}/BEST_WALK_ONNX_2.onnx",
    )
    parser.add_argument(
        "--duck_config_path",
        type=str,
        required=False,
        default=f"{HOME_DIR}/duck_config.json",
    )
    parser.add_argument("-a", "--action_scale", type=float, default=0.25)
    parser.add_argument("-p", type=int, default=30)
    parser.add_argument("-i", type=int, default=0)
    parser.add_argument("-d", type=int, default=0)
    parser.add_argument("-c", "--control_freq", type=int, default=50)
    parser.add_argument("--pitch_bias", type=float, default=0, help="deg")
    parser.add_argument(
        "--commands",
        action="store_true",
        default=True,
        help="external commands, keyboard or gamepad. For a gamepad plugged into a "
        "separate PC, enable remote_control in duck_config.json and run the "
        "remote_control PC-side app",
    )
    parser.add_argument(
        "--save_obs",
        type=str,
        required=False,
        default=False,
        help="save the run's observations",
    )
    parser.add_argument(
        "--replay_obs",
        type=str,
        required=False,
        default=None,
        help="replay the observations from a previous run (can be from the robot or from mujoco)",
    )
    parser.add_argument("--cutoff_frequency", type=float, default=None)
    parser.add_argument(
        "--head_only",
        action="store_true",
        default=False,
        help="Head-puppet mode: gamepad only drives the head, not locomotion",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        help="Override log level from config: TRACE, DEBUG, INFO, WARNING, ERROR",
    )

    args = parser.parse_args()

    # Resolve log level: CLI flag > duck_config.json > "INFO"
    log_level = "INFO"
    try:
        import json as _json

        _cfg = _json.load(open(args.duck_config_path))
        log_level = _cfg.get("log_level", log_level)
    except Exception:
        pass
    if args.log_level:
        log_level = args.log_level
    setup_logging(log_level)

    pid = [args.p, args.i, args.d]

    logger.debug("Args: %s", args)
    rl_walk = RLWalk(
        args.onnx_model_path,
        duck_config_path=args.duck_config_path,
        action_scale=args.action_scale,
        pid=pid,
        control_freq=args.control_freq,
        commands=args.commands,
        pitch_bias=args.pitch_bias,
        save_obs=args.save_obs,
        replay_obs=args.replay_obs,
        cutoff_frequency=args.cutoff_frequency,
        head_only=args.head_only,
    )
    logger.debug("RLWalk ready")
    restart_requested = rl_walk.run()
    if restart_requested:
        # Non-zero exit lets a process supervisor (e.g. systemd Restart=on-failure)
        # relaunch us into a fresh mode-selection state.
        sys.exit(1)


if __name__ == "__main__":
    main()
