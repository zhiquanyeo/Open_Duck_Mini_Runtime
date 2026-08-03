"""Open Duck Mini runtime. Import submodules directly, e.g.
`from open_duck_mini_runtime.hardware.hwi import HWI` — this package's
__init__.py intentionally does not eagerly re-export anything, so pure-logic
submodules (command_shaping, imu_trim, stability_governor, control_bus,
battery, ...) stay importable without pulling in hardware-only dependencies
(adafruit_bno055, RPi.GPIO, ...) that aren't installed off a Raspberry Pi."""
