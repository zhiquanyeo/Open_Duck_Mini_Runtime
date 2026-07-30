"""
Mirrors open_duck_mini_runtime.controller.command_shaping — duplicated here
(not imported) so this package stays install-able on a PC without the duck
runtime's heavy/hardware-only dependencies. Only used by fake_host.py to
preview what the duck would actually compute from a given packet; the real
duck is always the one enforcing these ranges on a live robot.
"""

X_RANGE = [-0.15, 0.15]
Y_RANGE = [-0.2, 0.2]
YAW_RANGE = [-1.0, 1.0]

# rads
NECK_PITCH_RANGE = [-0.34, 1.1]
HEAD_PITCH_RANGE = [-0.78, 0.3]
HEAD_YAW_RANGE = [-0.5, 0.5]
HEAD_ROLL_RANGE = [-0.5, 0.5]


def shape_commands(l_x, l_y, r_x, head_control_mode, last_commands):
    """Turn normalized joystick axes (-1..1, already sign-flipped) into the
    7-element RLWalk command vector, clamped to the ranges above.

    Mutates and returns `last_commands` in place — slots the current mode
    doesn't touch (e.g. index 3 outside head-control mode) keep whatever
    value they already had.
    """
    if not head_control_mode:
        lin_vel_y = l_x
        lin_vel_x = l_y
        ang_vel = r_x
        if lin_vel_x >= 0:
            lin_vel_x *= abs(X_RANGE[1])
        else:
            lin_vel_x *= abs(X_RANGE[0])

        if lin_vel_y >= 0:
            lin_vel_y *= abs(Y_RANGE[1])
        else:
            lin_vel_y *= abs(Y_RANGE[0])

        if ang_vel >= 0:
            ang_vel *= abs(YAW_RANGE[1])
        else:
            ang_vel *= abs(YAW_RANGE[0])

        last_commands[0] = lin_vel_x
        last_commands[1] = lin_vel_y
        last_commands[2] = ang_vel
    else:
        last_commands[0] = 0.0
        last_commands[1] = 0.0
        last_commands[2] = 0.0
        last_commands[3] = 0.0  # neck pitch 0 for now

        head_yaw = l_x
        head_pitch = l_y
        head_roll = r_x

        if head_yaw >= 0:
            head_yaw *= abs(HEAD_YAW_RANGE[0])
        else:
            head_yaw *= abs(HEAD_YAW_RANGE[1])

        if head_pitch >= 0:
            head_pitch *= abs(HEAD_PITCH_RANGE[0])
        else:
            head_pitch *= abs(HEAD_PITCH_RANGE[1])

        if head_roll >= 0:
            head_roll *= abs(HEAD_ROLL_RANGE[0])
        else:
            head_roll *= abs(HEAD_ROLL_RANGE[1])

        last_commands[4] = head_pitch
        last_commands[5] = head_yaw
        last_commands[6] = head_roll

    return last_commands
