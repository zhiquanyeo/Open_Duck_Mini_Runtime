"""Pure-logic tests for open_duck_mini_runtime.rl_walk.stability_governor."""

from open_duck_mini_runtime.rl_walk.stability_governor import (
    StabilityGovernor,
    tilt_angle_deg,
    tilt_rate,
    governor_from_config,
    accel_pitch_roll,
)


def test_disabled_by_default_is_pass_through():
    g = StabilityGovernor()
    assert g.enabled is False
    for tilt, rate in [(0, 0), (30, 10), (90, 20)]:
        assert g.update(tilt, rate) == 1.0


def test_governor_from_config_empty_dict_is_safe_default():
    g = governor_from_config({})
    assert g.enabled is False
    assert g.update(45, 10) == 1.0


def test_governor_from_config_none_is_safe_default():
    g = governor_from_config(None)
    assert g.enabled is False


def test_enabled_scales_down_when_tipping():
    g = StabilityGovernor(
        enabled=True, tilt_lo_deg=10, tilt_hi_deg=20, rate_lo=1, rate_hi=5,
        floor=0.2, smooth=1.0,  # smooth=1.0 -> jump straight to target, easier to assert
    )
    # Below both thresholds -> full speed.
    assert g.update(0, 0) == 1.0
    # Above both thresholds -> floor.
    assert abs(g.update(30, 10) - 0.2) < 1e-9


def test_enabled_mid_ramp_is_between_full_and_floor():
    g = StabilityGovernor(
        enabled=True, tilt_lo_deg=10, tilt_hi_deg=20, rate_lo=100, rate_hi=200,
        floor=0.2, smooth=1.0,
    )
    scale = g.update(15, 0)  # halfway through the tilt ramp, rate term inactive
    assert 0.2 < scale < 1.0


def test_severity_of_is_worse_of_tilt_and_rate():
    g = StabilityGovernor(tilt_lo_deg=10, tilt_hi_deg=20, rate_lo=1, rate_hi=5)
    # Tilt saturated, rate not.
    assert g.severity_of(30, 0) == 1.0
    # Rate saturated, tilt not.
    assert g.severity_of(0, 10) == 1.0
    # Neither.
    assert g.severity_of(0, 0) == 0.0


def test_tilt_angle_deg_pythagorean():
    assert tilt_angle_deg(3, 4) == 5.0


def test_tilt_rate_from_gyro_xy():
    assert tilt_rate([3, 4]) == 5.0


def test_accel_pitch_roll_none_when_missing():
    assert accel_pitch_roll(None) is None
    assert accel_pitch_roll([1, 2]) is None


def test_accel_pitch_roll_level():
    d = accel_pitch_roll([0.0, 0.0, 9.8])
    assert abs(d["pitch"]) < 1.0
    assert abs(d["roll"]) < 1.0
