"""Hardware interface: motors, IMU, sensors, LEDs, audio.

Import submodules directly (e.g. `from open_duck_mini_runtime.hardware.hwi
import HWI`) — nothing here is re-exported at the package level, so pure-logic
submodules like imu_trim stay importable without the hardware-only
dependencies (adafruit_bno055, RPi.GPIO, ...) most of this package needs."""
