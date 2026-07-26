"""
Boot-time supervisor for the Open Duck Mini.

Meant to be the process launched at system startup (see the systemd unit in
``systemd/duck-startup.service``). Drives the eyes through a small state
machine while waiting for a gamepad, lets the user pick a mode with the
gamepad, then hands off into RLWalk's own run loop for the rest of the
process lifetime:

  no gamepad         -> eyes fade orange, retrying connection
  gamepad connected  -> eyes fade green, waiting for A or X
  A pressed          -> eyes blink green x3, then normal walk mode
  X pressed          -> eyes blink purple x3, then head-puppet mode

Gamepad detection/button reads here use pygame directly rather than
XBoxController, so there is only ever one process touching the joystick
subsystem at a time. Once a mode is chosen, this script stops polling
pygame and RLWalk's own XBoxController takes over the joystick from
scratch.
"""

import argparse
import logging
import os
import sys
import time

import pygame

from open_duck_mini_runtime.duck_config import DuckConfig
from open_duck_mini_runtime.hardware.eyes import Eyes
from open_duck_mini_runtime.log import setup_logging
from open_duck_mini_runtime.rl_walk.walk import RLWalk

logger = logging.getLogger(__name__)

HOME_DIR = os.path.expanduser("~")

ORANGE = (255, 110, 0)
GREEN = (0, 255, 0)
PURPLE = (170, 0, 255)

# Matches the button layout XBoxController assumes elsewhere in the codebase.
_A_BUTTON = 0
_X_BUTTON = 3

_FADE_MIN = 0.05
_FADE_MAX = 1.0
_FADE_STEP = 0.04
_FADE_TICK = 0.03

_RECONNECT_CHECK_INTERVAL = 0.5

_BLINK_COUNT = 3
_BLINK_ON = 0.15
_BLINK_OFF = 0.15


class _Fader:
    """Breathing (fade in/out) brightness ramp on the eyes, one `tick()` per frame."""

    def __init__(self, eyes: Eyes | None, color):
        self.eyes = eyes
        self._brightness = _FADE_MIN
        self._direction = 1
        if self.eyes is not None:
            self.eyes.set_color(color)

    def tick(self) -> None:
        if self.eyes is None:
            return
        self.eyes.set_eyes_brightness(self._brightness)
        self._brightness += self._direction * _FADE_STEP
        if self._brightness >= _FADE_MAX:
            self._brightness = _FADE_MAX
            self._direction = -1
        elif self._brightness <= _FADE_MIN:
            self._brightness = _FADE_MIN
            self._direction = 1


def _blink(eyes: Eyes | None, color) -> None:
    if eyes is None:
        return
    eyes.set_color(color)
    for _ in range(_BLINK_COUNT):
        eyes.set_eyes_brightness(_FADE_MAX)
        time.sleep(_BLINK_ON)
        eyes.set_eyes_brightness(0.0)
        time.sleep(_BLINK_OFF)
    eyes.set_eyes_brightness(_FADE_MAX)


def _joystick_available() -> bool:
    pygame.joystick.quit()
    pygame.joystick.init()
    return pygame.joystick.get_count() > 0


def _wait_for_gamepad(eyes: Eyes | None) -> None:
    logger.info("Waiting for gamepad...")
    fader = _Fader(eyes, ORANGE)
    last_check = 0.0
    while True:
        now = time.time()
        if now - last_check >= _RECONNECT_CHECK_INTERVAL:
            last_check = now
            if _joystick_available():
                logger.info("Gamepad connected")
                return
        fader.tick()
        time.sleep(_FADE_TICK)


def _wait_for_mode_selection(eyes: Eyes | None) -> str:
    """Returns "walk" or "head_puppet"; "" if the gamepad dropped out."""
    logger.info("Waiting for A (walk) or X (head puppet)...")
    fader = _Fader(eyes, GREEN)
    joystick = pygame.joystick.Joystick(0)
    joystick.init()
    last_check = 0.0
    while True:
        now = time.time()
        if now - last_check >= _RECONNECT_CHECK_INTERVAL:
            last_check = now
            if pygame.joystick.get_count() == 0:
                logger.info("Gamepad disconnected, back to standby")
                return ""

        for event in pygame.event.get():
            if event.type != pygame.JOYBUTTONDOWN:
                continue
            if joystick.get_numbuttons() > _A_BUTTON and joystick.get_button(
                _A_BUTTON
            ):
                return "walk"
            if joystick.get_numbuttons() > _X_BUTTON and joystick.get_button(
                _X_BUTTON
            ):
                return "head_puppet"

        fader.tick()
        time.sleep(_FADE_TICK)


def run(onnx_model_path: str, duck_config_path: str) -> None:
    duck_config = DuckConfig(config_json_path=duck_config_path)

    eyes = None
    if duck_config.eyes:
        eyes = Eyes(neopixels=duck_config.neopixels, led_counts=duck_config.led_counts)
        # Take manual control of colour/brightness for the standby animation;
        # RLWalk creates its own Eyes wrapper once we hand off below.
        eyes.set_solid(True)

    pygame.init()

    try:
        mode = ""
        while not mode:
            _wait_for_gamepad(eyes)
            mode = _wait_for_mode_selection(eyes)

        _blink(eyes, GREEN if mode == "walk" else PURPLE)

        rl_walk = RLWalk(
            onnx_model_path,
            duck_config_path=duck_config_path,
            commands=True,
            head_only=(mode == "head_puppet"),
        )
        restart_requested = rl_walk.run()
        if restart_requested:
            # RLWalk has already turned off motors and stopped its own
            # eyes/antennas/projector/feet_contacts. Exit non-zero so the
            # service supervisor (systemd Restart=on-failure) relaunches this
            # whole process fresh — that's what guarantees the servo serial
            # port and IMU I2C thread are actually released before the next
            # RLWalk tries to open them again.
            logger.info("Restart requested — exiting for supervisor relaunch")
            sys.exit(1)
    except KeyboardInterrupt:
        if eyes is not None:
            eyes.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--onnx_model_path",
        type=str,
        default=f"{HOME_DIR}/BEST_WALK_ONNX_2.onnx",
    )
    parser.add_argument(
        "--duck_config_path",
        type=str,
        default=f"{HOME_DIR}/duck_config.json",
    )
    parser.add_argument("--log-level", type=str, default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)
    run(args.onnx_model_path, args.duck_config_path)


if __name__ == "__main__":
    main()
