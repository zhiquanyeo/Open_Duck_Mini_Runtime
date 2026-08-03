"""
Canonical WALK TUNING defaults — in ONE place so the Web UI "reset to defaults"
button and any docs all agree, instead of people hunting the values down.

These mirror the "Supercharged" fork's reference-tuned values, EXCEPT
stability_governor.enabled, which ships False here (this project's config
philosophy is opt-in/conservative defaults — see remote_control.enabled,
web_stats.enabled, etc. in example_config.json). The governor's own tuned
numbers are still the values to dial in if you enable it.
"""

WALK_TUNING_DEFAULTS = {
    "action_scale": 0.25,
    "phase_frequency_factor_offset": 0.0,
    "velocity_clip": False,
    "max_motor_velocity_rad_s": 5.24,
    "stability_governor": {
        "enabled": False,
        "tilt_lo_deg": 10.0,
        "tilt_hi_deg": 25.0,
        "rate_lo": 2.5,
        "rate_hi": 7.0,
        "floor": 0.35,
        "smooth": 0.3,
    },
}

# "Reset" target for the IMU mounting trim: neutral (no trim). Reset means back to
# the 0 baseline you'd re-measure from if a manual tweak went wrong.
IMU_TRIM_DEFAULTS = {"pitch": 0.0, "roll": 0.0}

# "Reset" target for per-segment LED brightness: full brightness, same as
# duck_config.py's own led_brightness defaults.
LED_BRIGHTNESS_DEFAULTS = {"projector": 1.0, "right_eye": 1.0, "left_eye": 1.0}

# "Reset" target for speaker volume: full volume, same as duck_config.py's own
# speaker_volume default.
SPEAKER_DEFAULTS = {"volume": 1.0}
