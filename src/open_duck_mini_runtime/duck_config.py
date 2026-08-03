import json
import logging
import shutil
import time
from typing import Optional
import os

logger = logging.getLogger(__name__)

HOME_DIR = os.path.expanduser("~")

LED_ORDER: str = os.getenv("ODUCK_LED_ORDER", "GRB").upper()


def save_config_fields(
    updates: dict, config_json_path: str = f"{HOME_DIR}/duck_config.json", backup=True
):
    """Merge `updates` (a dict of top-level keys) into the config file, preserving
    every OTHER field, and write it back. Backs the file up first (timestamped) so
    a bad write is always recoverable. Returns the backup path (or None if there
    was no existing file). Used by the web control UI to persist live IMU-trim /
    walk-tuning edits without disturbing the rest of duck_config.json."""
    try:
        cfg = json.load(open(config_json_path, "r"))
    except FileNotFoundError:
        cfg = {}
    backup_path = None
    if backup and os.path.exists(config_json_path):
        backup_path = f"{config_json_path}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(config_json_path, backup_path)
    cfg.update(updates)
    with open(config_json_path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    return backup_path


class DuckConfig:

    def __init__(
        self,
        config_json_path: Optional[str] = f"{HOME_DIR}/duck_config.json",
        ignore_default: bool = False,
    ):
        """
        Looks for duck_config.json in the home directory by default.
        If not found, uses default values.
        """
        self.default = False
        try:
            self.json_config = (
                json.load(open(config_json_path, "r")) if config_json_path else {}
            )
        except FileNotFoundError:
            logger.warning(
                "config json not found at %s, using defaults", config_json_path
            )
            self.json_config = {}
            self.default = True

        if config_json_path is None:
            logger.warning("no config json path provided, using defaults")
            self.default = True

        if self.default and not ignore_default:
            logger.warning(
                "Running with default values — this probably won't work well. "
                "Please create a duck_config.json file."
            )
            res = input("Do you still want to run? (y/N) ")
            if res.lower() != "y":
                logger.info("Exiting at user request")
                exit(1)

        self.log_level = self.json_config.get("log_level", "INFO")
        self.start_paused = self.json_config.get("start_paused", False)
        self.imu_upside_down = self.json_config.get("imu_upside_down", False)
        self.fall_detection = self.json_config.get("fall_detection", True)
        self.fall_threshold_deg = self.json_config.get("fall_threshold_deg", 45)
        self.phase_frequency_factor_offset = self.json_config.get(
            "phase_frequency_factor_offset", 0.0
        )

        eye_colors = self.json_config.get("eye_colors", {})
        self.eye_color_start = eye_colors.get(
            "start", [255, 255, 255]
        )  # white — walking
        self.eye_color_paused = eye_colors.get(
            "paused", [255, 105, 180]
        )  # hot pink — paused. why? because my fiance likes hot pink and i like her, so hot pink it is. todo: configure from json
        self.eye_color_off = eye_colors.get("off", [255, 0, 0])  # red — motors off

        expression_features = self.json_config.get("expression_features", {})

        self.eyes = expression_features.get("eyes", False)
        # neopixels=True  → use the NeoPixel LED strip (new hardware, default)
        # neopixels=False → use original single-colour GPIO eyes (old hardware)
        self.neopixels = expression_features.get("neopixels", True)
        self.projector = expression_features.get("projector", False)
        self.antennas = expression_features.get("antennas", False)
        self.speaker = expression_features.get("speaker", False)
        self.microphone = expression_features.get("microphone", False)
        self.camera = expression_features.get("camera", False)

        web_stats = self.json_config.get("web_stats", {})
        self.web_stats_enabled = web_stats.get("enabled", False)
        self.web_stats_port = web_stats.get("port", 8080)

        remote_control = self.json_config.get("remote_control", {})
        self.remote_control_enabled = remote_control.get("enabled", False)
        self.remote_control_port = remote_control.get("port", 10000)

        imu_trim = self.json_config.get("imu_trim", {})
        self.imu_trim = {
            "pitch": imu_trim.get("pitch", 0.0),
            "roll": imu_trim.get("roll", 0.0),
        }

        # Raw dict — governor_from_config() (rl_walk/stability_governor.py) fills
        # in safe defaults for any missing keys. Ships disabled (pure pass-through)
        # regardless of what's in the config unless explicitly set true.
        self.stability_governor = self.json_config.get("stability_governor", {})

        # Optional live-tunable walk settings; fall back to the code defaults used
        # elsewhere (RLWalk.__init__'s action_scale arg, the hardcoded
        # max_motor_velocity) when absent from config.
        self.action_scale = self.json_config.get("action_scale", None)
        self.velocity_clip = self.json_config.get("velocity_clip", False)
        self.max_motor_velocity_rad_s = self.json_config.get(
            "max_motor_velocity_rad_s", 5.24
        )

        # Raw dict — battery.py's DEFAULT_V_MIN/V_MAX/V_FULL fill in anything
        # missing when the estimator is constructed.
        self.battery = self.json_config.get("battery", {})

        self.led_order = self.json_config.get("led_order", "GRBW")

        led_counts = self.json_config.get("led_counts", {})
        self.led_counts = {
            "projector": led_counts.get("projector", 1),
            "right_eye": led_counts.get("right_eye", 1),
            "left_eye": led_counts.get("left_eye", 1),
        }

        # default joints offsets are 0.0
        self.joints_offset = self.json_config.get(
            "joints_offsets",
            {
                "left_hip_yaw": 0.0,
                "left_hip_roll": 0.0,
                "left_hip_pitch": 0.0,
                "left_knee": 0.0,
                "left_ankle": 0.0,
                "neck_pitch": 0.0,
                "head_pitch": 0.0,
                "head_yaw": 0.0,
                "head_roll": 0.00,
                "right_hip_yaw": 0.0,
                "right_hip_roll": 0.0,
                "right_hip_pitch": 0.0,
                "right_knee": 0.0,
                "right_ankle": 0.0,
            },
        )
