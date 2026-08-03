import logging
import time

import numpy as np
import rustypot
from open_duck_mini_runtime.duck_config import DuckConfig  # top-level, not in hardware/

logger = logging.getLogger(__name__)


class HWI:
    # Feetech present-voltage/temperature registers report volts in units of
    # 0.1V (matches tools/check_voltage.py). If a future rustypot build starts
    # returning volts directly, set this to 1.0.
    VOLTAGE_SCALE = 0.1

    def __init__(self, duck_config: DuckConfig, usb_port: str = "/dev/ttyACM0"):

        self.duck_config = duck_config

        # Order matters here
        self.joints = {
            "left_hip_yaw": 20,
            "left_hip_roll": 21,
            "left_hip_pitch": 22,
            "left_knee": 23,
            "left_ankle": 24,
            "neck_pitch": 30,
            "head_pitch": 31,
            "head_yaw": 32,
            "head_roll": 33,
            # "left_antenna": None,
            # "right_antenna": None,
            "right_hip_yaw": 10,
            "right_hip_roll": 11,
            "right_hip_pitch": 12,
            "right_knee": 13,
            "right_ankle": 14,
        }

        self.zero_pos = {
            "left_hip_yaw": 0,
            "left_hip_roll": 0,
            "left_hip_pitch": 0,
            "left_knee": 0,
            "left_ankle": 0,
            "neck_pitch": 0,
            "head_pitch": 0,
            "head_yaw": 0,
            "head_roll": 0,
            # "left_antenna":0,
            # "right_antenna":0,
            "right_hip_yaw": 0,
            "right_hip_roll": 0,
            "right_hip_pitch": 0,
            "right_knee": 0,
            "right_ankle": 0,
        }

        self.init_pos = {
            "left_hip_yaw": 0.002,
            "left_hip_roll": 0.053,
            "left_hip_pitch": -0.63,
            "left_knee": 1.368,
            "left_ankle": -0.784,
            "neck_pitch": 0.0,
            "head_pitch": 0.0,
            "head_yaw": 0,
            "head_roll": 0,
            # "left_antenna": 0,
            # "right_antenna": 0,
            "right_hip_yaw": -0.003,
            "right_hip_roll": -0.065,
            "right_hip_pitch": 0.635,
            "right_knee": 1.379,
            "right_ankle": -0.796,
        }

        self.joints_offsets = self.duck_config.joints_offset

        self.kps = np.ones(len(self.joints)) * 32  # default kp
        self.kds = np.ones(len(self.joints)) * 0  # default kd
        self.low_torque_kps = np.ones(len(self.joints)) * 2

        self.usb_port = usb_port
        self.io = rustypot.feetech(usb_port, 1000000)

    def set_kps(self, kps):
        self.kps = kps
        joint_ids = list(self.joints.values())
        for i, motor_id in enumerate(joint_ids):
            self.io.set_kps([motor_id], [self.kps[i]])

    def set_kds(self, kds):
        self.kds = kds
        joint_ids = list(self.joints.values())
        for i, motor_id in enumerate(joint_ids):
            self.io.set_kds([motor_id], [self.kds[i]])

    def set_kp(self, id, kp):
        self.io.set_kps([id], [kp])

    def turn_on(self):
        joint_ids = list(self.joints.values())
        for i, motor_id in enumerate(joint_ids):
            self.io.set_kps([motor_id], [self.low_torque_kps[i]])
        logger.info("turn on: low kps set")
        time.sleep(1)

        self.set_position_all(self.init_pos)
        logger.info("turn on: init pos set")

        time.sleep(1)

        for i, motor_id in enumerate(joint_ids):
            self.io.set_kps([motor_id], [self.kps[i]])
        logger.info("turn on: high kps set")

    def turn_off(self):
        for motor_id in self.joints.values():
            self.io.disable_torque([motor_id])

    def set_position(self, joint_name, pos):
        """
        pos is in radians
        """
        id = self.joints[joint_name]
        pos = pos + self.joints_offsets[joint_name]
        self.io.write_goal_position([id], [pos])

    def set_position_all(self, joints_positions):
        """
        joints_positions is a dictionary with joint names as keys and joint positions as values
        Warning: expects radians
        """
        for joint, position in joints_positions.items():
            motor_id = self.joints[joint]
            pos = position + self.joints_offsets[joint]
            self.io.write_goal_position([motor_id], [pos])

    def scan_servos(self) -> dict:
        """Ping each servo individually and return {joint_name: present} dict."""
        results = {}
        for name, servo_id in self.joints.items():
            try:
                result = self.io.read_present_position([servo_id])
                results[name] = result is not None and len(result) > 0
            except Exception:
                results[name] = False
        return results

    def get_present_positions(self, ignore=[]):
        """
        Returns the present positions in radians
        """
        positions = []
        for joint, motor_id in self.joints.items():
            if joint in ignore:
                continue
            try:
                result = self.io.read_present_position([motor_id])
                if result is None or len(result) == 0:
                    logger.warning("read_present_position empty for %s", joint)
                    return None
                positions.append(result[0] - self.joints_offsets[joint])
            except Exception as e:
                logger.warning("read_present_position failed for %s: %s", joint, e)
                return None
        return np.array(np.around(positions, 3))

    def get_present_velocities(self, rad_s=True, ignore=[]):
        """
        Returns the present velocities in rad/s (default) or rev/min
        """
        velocities = []
        for joint, motor_id in self.joints.items():
            if joint in ignore:
                continue
            try:
                result = self.io.read_present_velocity([motor_id])
                if result is None or len(result) == 0:
                    logger.warning("read_present_velocity empty for %s", joint)
                    return None
                velocities.append(result[0])
            except Exception as e:
                logger.warning("read_present_velocity failed for %s: %s", joint, e)
                return None
        return np.array(np.around(velocities, 3))

    def get_present_voltage(self):
        """
        Mean bus voltage (V) read through the SAME rustypot connection the loop
        already owns — the serial bus is single-owner, so this never opens a
        second connection. Best effort: returns None if this rustypot build
        doesn't expose the register (true as of rustypot 0.1.0 — only
        position/velocity are bound), so callers must handle None gracefully.
        For an actual reading on builds without the register, see
        read_battery_handoff() below.
        """
        reader = getattr(self.io, "read_present_voltage", None)
        if reader is None:
            return None
        try:
            vals = reader(list(self.joints.values()))
        except Exception as e:
            logger.warning("voltage read failed: %s", e)
            return None
        vals = [float(v) * self.VOLTAGE_SCALE for v in vals if v is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    def get_present_temperature(self):
        """Hottest servo temperature (°C), or None if unavailable. Same
        guarded, single-connection approach as get_present_voltage."""
        reader = getattr(self.io, "read_present_temperature", None)
        if reader is None:
            return None
        try:
            vals = reader(list(self.joints.values()))
        except Exception as e:
            logger.warning("temperature read failed: %s", e)
            return None
        vals = [float(v) for v in vals if v is not None]
        if not vals:
            return None
        return round(max(vals), 1)

    def read_battery_handoff(self):
        """
        Read servo bus voltage (V, mean) + hottest temp (°C) via a brief BUS
        HANDOFF: rustypot 0.1.0 can't read those registers, so this
        momentarily RELEASES the serial port, reads them through pypot
        (which can), then ALWAYS re-acquires rustypot so the control loop
        keeps the bus.

        Costs ~0.2s during which NO goal positions are sent — the servos
        simply hold their last goal (torque + internal PID stay on). The
        CALLER must only invoke this when it's safe to skip writes briefly
        (paused/idle) — NEVER mid-stride. See RLWalk._sample_battery(),
        which only calls this from the paused branch of run(), throttled.

        Returns (voltage, temp), each possibly None. Single-threaded use
        only (call it from the control loop, not the HTTP handler thread —
        same rule as every other servo-bus read in this codebase).
        """
        import gc

        ids = list(self.joints.values())
        voltage = temp = None
        try:
            self.io = None  # drop the rustypot handle -> releases the port
            gc.collect()
            time.sleep(0.02)
            from pypot.feetech import FeetechSTS3215IO

            pio = FeetechSTS3215IO(self.usb_port, baudrate=1000000, use_sync_read=True)
            try:
                vv = pio.get_present_voltage(ids)
                if vv:
                    voltage = round(sum(vv) / len(vv) * self.VOLTAGE_SCALE, 2)
                try:
                    tt = pio.get_present_temperature(ids)
                    if tt:
                        temp = round(max(tt), 1)
                except Exception:
                    temp = None  # temp is a bonus, never block on it
            finally:
                try:
                    pio.close()
                except Exception:
                    pass
                pio = None
                gc.collect()
                time.sleep(0.02)
        except Exception as e:
            logger.warning("battery handoff read failed: %s", e)
        finally:
            # ALWAYS re-acquire rustypot so control resumes, even if pypot errored.
            if self.io is None:
                try:
                    self.io = rustypot.feetech(self.usb_port, 1000000)
                except Exception:
                    # One retry — losing the bus here is fatal to the loop.
                    time.sleep(0.1)
                    self.io = rustypot.feetech(self.usb_port, 1000000)
                try:
                    # Servos keep their gains across the handoff on their own;
                    # re-apply anyway, belt-and-braces. Reuses set_kps/set_kds
                    # (per-motor calls) rather than guessing at a batch-call
                    # signature this rustypot binding hasn't been exercised with.
                    self.set_kps(self.kps)
                    self.set_kds(self.kds)
                except Exception as e:
                    logger.warning("re-apply gains after handoff failed: %s", e)
        return voltage, temp
