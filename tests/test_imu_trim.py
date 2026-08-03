"""Pure-logic tests for open_duck_mini_runtime.hardware.imu_trim — no hardware."""

import numpy as np
import pytest

from open_duck_mini_runtime.hardware.imu_trim import (
    apply_trim,
    trim_from_gravity,
    gravity_tilt_deg,
    describe_gravity,
)


def test_apply_trim_identity_at_zero():
    v = [1.0, 2.0, 3.0]
    result = apply_trim(v, 0.0, 0.0)
    assert np.allclose(result, v)


def test_trim_from_gravity_nulls_horizontal_components():
    # A gravity reading tilted off Z (some x/y leakage from a mounting error).
    tilted = [1.2, -0.8, 9.6]
    pitch, roll = trim_from_gravity(tilted)
    corrected = apply_trim(tilted, pitch, roll)
    assert abs(corrected[0]) < 1e-6
    assert abs(corrected[1]) < 1e-6
    # Magnitude (the gravity pole) is preserved.
    assert np.isclose(np.linalg.norm(corrected), np.linalg.norm(tilted))


def test_trim_from_gravity_preserves_upside_down_pole():
    # Upside-down mount: dominant gravity component is -Z.
    tilted = [0.5, -0.3, -9.7]
    pitch, roll = trim_from_gravity(tilted)
    corrected = apply_trim(tilted, pitch, roll)
    assert corrected[2] < 0  # still negative — pole not flipped
    assert abs(corrected[0]) < 1e-6
    assert abs(corrected[1]) < 1e-6


def test_trim_from_gravity_degenerate_returns_zero():
    # No dominant Z component -> (0, 0), not a wild extrapolation.
    assert trim_from_gravity([1.0, 1.0, 0.0]) == (0.0, 0.0)


def test_gravity_tilt_deg_matches_radians():
    tilted = [1.2, -0.8, 9.6]
    p_rad, r_rad = trim_from_gravity(tilted)
    p_deg, r_deg = gravity_tilt_deg(tilted)
    assert np.isclose(np.radians(p_deg), p_rad)
    assert np.isclose(np.radians(r_deg), r_rad)


@pytest.mark.parametrize(
    "vec,expected_axis,expected_sign",
    [
        ([0.1, 0.2, 9.8], "z", "+"),
        ([0.1, 0.2, -9.8], "z", "-"),
        ([9.8, 0.1, 0.2], "x", "+"),
    ],
)
def test_describe_gravity(vec, expected_axis, expected_sign):
    d = describe_gravity(vec)
    assert d["dominant_axis"] == expected_axis
    assert d["sign"] == expected_sign
    assert d["z_dominant"] == (expected_axis == "z")
