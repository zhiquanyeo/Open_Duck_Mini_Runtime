"""Pure-logic tests for open_duck_mini_runtime.rl_walk.battery — no hardware."""

from open_duck_mini_runtime.rl_walk.battery import (
    estimate_percent,
    ChargeEstimator,
    DEFAULT_V_MIN,
    DEFAULT_V_MAX,
)


def test_estimate_percent_none_voltage():
    assert estimate_percent(None) is None


def test_estimate_percent_nonsense_voltage():
    assert estimate_percent("not a number") is None


def test_estimate_percent_clamped_to_0_100():
    assert estimate_percent(0.0) is None  # v <= 0 -> None (nonsensical read)
    assert estimate_percent(DEFAULT_V_MIN - 1) == 0
    assert estimate_percent(DEFAULT_V_MAX + 1) == 100


def test_estimate_percent_midpoint():
    mid = (DEFAULT_V_MIN + DEFAULT_V_MAX) / 2
    assert estimate_percent(mid) == 50


def test_charge_estimator_none_voltage_returns_none():
    ce = ChargeEstimator()
    assert ce.update(now=0.0, voltage=None) is None


def test_charge_estimator_above_v_full_is_charging():
    ce = ChargeEstimator(v_full=8.5)
    assert ce.update(now=0.0, voltage=8.6) is True


def test_charge_estimator_flat_voltage_not_charging():
    ce = ChargeEstimator(v_full=8.5, window_s=30.0, rise_threshold=0.05)
    ce.update(now=0.0, voltage=7.4)
    assert ce.update(now=1.0, voltage=7.4) is False


def test_charge_estimator_rising_voltage_is_charging():
    ce = ChargeEstimator(v_full=8.5, window_s=30.0, rise_threshold=0.05)
    ce.update(now=0.0, voltage=7.4)
    assert ce.update(now=1.0, voltage=7.5) is True


def test_charge_estimator_forgets_old_samples_outside_window():
    ce = ChargeEstimator(v_full=8.5, window_s=10.0, rise_threshold=0.05)
    ce.update(now=0.0, voltage=7.0)
    # Old sample falls out of the window by the time this one arrives.
    result = ce.update(now=100.0, voltage=7.02)
    assert result is False
