"""Pure-logic tests for open_duck_mini_runtime.rl_walk.control_bus — no hardware,
no sockets (that's stats_server.py's job)."""

from open_duck_mini_runtime.rl_walk.control_bus import ControlBus


def test_stick_override_inactive_before_any_command():
    bus = ControlBus()
    active, l_x, l_y, r_x, r_y, lt, rt = bus.stick_override(now=100.0)
    assert active is False
    assert (l_x, l_y, r_x, r_y, lt, rt) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_stick_override_active_when_fresh():
    bus = ControlBus(stale_s=0.5)
    bus.set_command(now=100.0, active=True, l_x=0.5, l_y=-0.5, r_x=0.2)
    active, l_x, l_y, r_x, r_y, lt, rt = bus.stick_override(now=100.1)
    assert active is True
    assert l_x == 0.5 and l_y == -0.5 and r_x == 0.2


def test_stick_override_goes_stale():
    bus = ControlBus(stale_s=0.5)
    bus.set_command(now=100.0, active=True, l_x=0.5)
    active, *_ = bus.stick_override(now=100.6)  # past stale_s
    assert active is False


def test_axes_and_triggers_are_clamped():
    bus = ControlBus()
    bus.set_command(now=0.0, active=True, l_x=5.0, l_y=-5.0, left_trigger=5.0, right_trigger=-5.0)
    _, l_x, l_y, _, _, lt, rt = bus.stick_override(now=0.0)
    assert l_x == 1.0
    assert l_y == -1.0
    assert lt == 1.0
    assert rt == 0.0


def test_push_button_rejects_unknown_name():
    bus = ControlBus()
    assert bus.push_button("NOT_A_BUTTON", "press") is False


def test_held_button_stays_pressed_across_ticks():
    bus = ControlBus()
    bus.push_button("A", "down")
    assert bus.consume_buttons()["A"] is True
    assert bus.consume_buttons()["A"] is True  # still held
    bus.push_button("A", "up")
    assert bus.consume_buttons()["A"] is False


def test_press_emits_one_edge_then_release():
    bus = ControlBus()
    bus.push_button("A", "press")
    ticks = [bus.consume_buttons()["A"] for _ in range(3)]
    assert ticks == [True, False, False]


def test_two_rapid_taps_emit_two_edges():
    bus = ControlBus()
    bus.push_button("A", "press")
    bus.push_button("A", "press")
    ticks = [bus.consume_buttons()["A"] for _ in range(4)]
    assert ticks == [True, False, True, False]


def test_consume_trim_accumulates_and_resets():
    bus = ControlBus()
    bus.push_trim("pitch", 0.01)
    bus.push_trim("pitch", 0.02)
    bus.push_trim("roll", -0.005)
    pitch, roll, save = bus.consume_trim()
    assert abs(pitch - 0.03) < 1e-9
    assert abs(roll - (-0.005)) < 1e-9
    assert save is False
    # Drained -> next consume is empty.
    pitch2, roll2, save2 = bus.consume_trim()
    assert (pitch2, roll2, save2) == (0.0, 0.0, False)


def test_trim_save_flag():
    bus = ControlBus()
    bus.push_trim_save()
    _, _, save = bus.consume_trim()
    assert save is True


def test_push_trim_rejects_unknown_axis():
    bus = ControlBus()
    assert bus.push_trim("yaw", 0.1) is False


def test_consume_settings_groups_and_clears():
    bus = ControlBus()
    bus.push_setting("walk", "action_scale", 0.3)
    bus.push_setting("walk", "velocity_clip", True)
    bus.push_setting_save("walk")
    bus.push_setting_reset("imu_trim")
    settings, saves, resets = bus.consume_settings()
    assert settings == {"walk": {"action_scale": 0.3, "velocity_clip": True}}
    assert saves == {"walk"}
    assert resets == {"imu_trim"}
    # Drained.
    settings2, saves2, resets2 = bus.consume_settings()
    assert settings2 == {} and saves2 == set() and resets2 == set()


def test_last_write_wins_per_key():
    bus = ControlBus()
    bus.push_setting("walk", "action_scale", 0.1)
    bus.push_setting("walk", "action_scale", 0.2)
    settings, _, _ = bus.consume_settings()
    assert settings["walk"]["action_scale"] == 0.2
