import logging
import adafruit_bno055
import board
import busio
import numpy as np
import os
import pickle

from queue import Queue
from threading import Thread
import time

from open_duck_mini_runtime.hardware.imu_trim import apply_trim

logger = logging.getLogger(__name__)


# TODO filter spikes
class Imu:
    def __init__(
        self,
        sampling_freq,
        user_pitch_bias=0,
        calibrate=False,
        upside_down=True,
        pitch_trim=0.0,
        roll_trim=0.0,
    ):
        self.sampling_freq = sampling_freq
        self.calibrate = calibrate
        # Residual mounting-tilt trim (radians) applied to accel/gyro/gravity after
        # the axis remap below. Default 0 -> identity. The legacy user_pitch_bias
        # (degrees) folds in as extra pitch so that param is no longer a no-op.
        self.pitch_trim = float(pitch_trim) + float(np.radians(user_pitch_bias))
        self.roll_trim = float(roll_trim)

        i2c = busio.I2C(board.SCL, board.SDA)
        self.imu = adafruit_bno055.BNO055_I2C(i2c)

        # self.imu.mode = adafruit_bno055.IMUPLUS_MODE
        # self.imu.mode = adafruit_bno055.ACCGYRO_MODE
        # self.imu.mode = adafruit_bno055.GYRONLY_MODE
        self.imu.mode = adafruit_bno055.NDOF_MODE
        # self.imu.mode = adafruit_bno055.NDOF_FMC_OFF_MODE

        if upside_down:
            self.imu.axis_remap = (
                adafruit_bno055.AXIS_REMAP_Y,
                adafruit_bno055.AXIS_REMAP_X,
                adafruit_bno055.AXIS_REMAP_Z,
                adafruit_bno055.AXIS_REMAP_NEGATIVE,
                adafruit_bno055.AXIS_REMAP_NEGATIVE,
                adafruit_bno055.AXIS_REMAP_NEGATIVE,
            )

        else:
            self.imu.axis_remap = (
                adafruit_bno055.AXIS_REMAP_Y,
                adafruit_bno055.AXIS_REMAP_X,
                adafruit_bno055.AXIS_REMAP_Z,
                adafruit_bno055.AXIS_REMAP_NEGATIVE,
                adafruit_bno055.AXIS_REMAP_POSITIVE,
                adafruit_bno055.AXIS_REMAP_POSITIVE,
            )

        if self.calibrate:
            self.imu.mode = adafruit_bno055.NDOF_MODE
            calibrated = self.imu.calibrated
            while not calibrated:
                logger.info("Calibration status: %s", self.imu.calibration_status)
                logger.info("Calibrated: %s", self.imu.calibrated)
                calibrated = self.imu.calibrated
                time.sleep(0.1)
            logger.info("IMU calibration done")
            offsets_accelerometer = self.imu.offsets_accelerometer
            offsets_gyroscope = self.imu.offsets_gyroscope
            offsets_magnetometer = self.imu.offsets_magnetometer

            imu_calib_data = {
                "offsets_accelerometer": offsets_accelerometer,
                "offsets_gyroscope": offsets_gyroscope,
                "offsets_magnetometer": offsets_magnetometer,
            }
            for k, v in imu_calib_data.items():
                logger.debug("  %s: %s", k, v)

            pickle.dump(imu_calib_data, open("imu_calib_data.pkl", "wb"))

            logger.info("Saved imu_calib_data.pkl")
            exit()

        if os.path.exists("imu_calib_data.pkl"):
            imu_calib_data = pickle.load(open("imu_calib_data.pkl", "rb"))
            self.imu.mode = adafruit_bno055.CONFIG_MODE
            time.sleep(0.1)
            self.imu.offsets_accelerometer = imu_calib_data["offsets_accelerometer"]
            self.imu.offsets_gyroscope = imu_calib_data["offsets_gyroscope"]
            self.imu.offsets_magnetometer = imu_calib_data["offsets_magnetometer"]
            self.imu.mode = adafruit_bno055.NDOF_MODE
            time.sleep(0.1)
        else:
            logger.warning("imu_calib_data.pkl not found — IMU running uncalibrated")

        self.x_offset = 0

        # self.tare_x()

        self.last_imu_data = {
            "gyro": [0, 0, 0],
            "accelero": [0, 0, 0],
            "gravity": [0, 0, 0],
        }
        self.imu_queue = Queue(maxsize=1)
        Thread(target=self.imu_worker, daemon=True).start()

    def tare_x(self):
        print("Taring x ...")
        x_values = []
        num_values = 100
        ok = False
        while not ok:
            x_values.append(np.array(self.imu.acceleration)[0])

            x_values = x_values[-num_values:]

            if len(x_values) == num_values:
                mean = np.mean(x_values)
                std = np.std(x_values)
                if std < 0.05:
                    ok = True
                    self.x_offset = mean
                    print("Tare x done")
                else:
                    print(std)

            time.sleep(0.01)

    def imu_worker(self):
        while True:
            s = time.time()
            try:
                gyro = np.array(self.imu.gyro).copy()
                accelero = np.array(self.imu.acceleration).copy()
                gravity = np.array(self.imu.gravity).copy()
            except Exception as e:
                logger.warning("IMU read error: %s", e)
                continue

            if gyro is None or accelero is None or gravity is None:
                continue

            if gyro.any() is None or accelero.any() is None or gravity.any() is None:
                continue

            accelero[0] -= self.x_offset

            # Correct residual IMU mounting tilt (same rigid rotation on accel,
            # gyro, and gravity — they share the sensor->body frame). Default
            # trim is 0 -> this is a no-op.
            if self.pitch_trim or self.roll_trim:
                accelero = apply_trim(accelero, self.pitch_trim, self.roll_trim)
                gyro = apply_trim(gyro, self.pitch_trim, self.roll_trim)
                gravity = apply_trim(gravity, self.pitch_trim, self.roll_trim)

            data = {
                "gyro": gyro,
                "accelero": accelero,
                "gravity": gravity,
            }

            self.imu_queue.put(data)
            took = time.time() - s
            time.sleep(max(0, 1 / self.sampling_freq - took))

    def get_data(self):
        try:
            self.last_imu_data = self.imu_queue.get(False)  # non blocking
        except Exception:
            pass

        return self.last_imu_data


if __name__ == "__main__":
    imu = Imu(50, upside_down=False)
    while True:
        data = imu.get_data()
        # print(data)
        print("gyro", np.around(data["gyro"], 3))
        print("accelero", np.around(data["accelero"], 3))
        print("---")
        time.sleep(1 / 25)
