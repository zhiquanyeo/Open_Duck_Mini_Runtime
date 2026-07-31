"""
Local gamepad reader for the PC-side relay.

Deliberately uses pygame's SDL_GameController API (`pygame._sdl2.controller`)
rather than the raw `pygame.joystick` button-index API that
open_duck_mini_runtime.controller.xbox_controller uses on the duck. Raw
joystick button indices are driver/platform-dependent (the duck's indices
match Xbox-controller-over-Bluetooth-via-xpadneo on Linux specifically);
SDL_GameController normalizes physical buttons to logical names
(A/B/X/Y/LEFTSHOULDER/...) the same way on Windows, Linux, and SteamOS, via
SDL's built-in controller database. That's what makes this reader portable
across desktop PC, WSL-with-USB-passthrough, and Steam Deck.

Note: in pygame 2.6.x this API lives at `pygame._sdl2.controller`, not the
top-level `pygame.controller` some older docs/examples reference. The
CONTROLLER_* button/axis constants are still on the top-level `pygame`
namespace though.
"""

import logging

import pygame
import pygame._sdl2.controller as sdl_controller

logger = logging.getLogger(__name__)

# Trigger axes read from SDL as -1 (released) .. 1 (fully pressed).
_TRIGGER_DEADZONE = 0.1


class GamepadReader:
    """Synchronous, single-controller reader. Call `poll()` once per tick."""

    def __init__(self, controller_index: int = 0):
        pygame.init()
        pygame.joystick.init()
        if not sdl_controller.get_init():
            sdl_controller.init()

        self.controller_index = controller_index
        self.controller = None
        self.connected = False
        self._try_connect()

    def _try_connect(self) -> bool:
        sdl_controller.quit()
        sdl_controller.init()
        if sdl_controller.get_count() <= self.controller_index:
            self.connected = False
            self.controller = None
            return False

        self.controller = sdl_controller.Controller(self.controller_index)
        self.controller.init()
        logger.info("Connected: %s", self.controller.name)
        self.connected = True
        return True

    def poll(self) -> dict | None:
        """Returns a packet dict matching what RemoteController expects, or
        None if no controller is connected (caller should skip sending)."""
        pygame.event.pump()

        if not self.connected or self.controller is None:
            if not self._try_connect():
                return None

        c = self.controller

        def trigger(axis):
            value = round((c.get_axis(axis) + 1) / 2, 3)
            return 0.0 if value < _TRIGGER_DEADZONE else value

        dpad_y = 0
        if c.get_button(pygame.CONTROLLER_BUTTON_DPAD_UP):
            dpad_y = 1
        elif c.get_button(pygame.CONTROLLER_BUTTON_DPAD_DOWN):
            dpad_y = -1

        dpad_x = 0
        if c.get_button(pygame.CONTROLLER_BUTTON_DPAD_RIGHT):
            dpad_x = 1
        elif c.get_button(pygame.CONTROLLER_BUTTON_DPAD_LEFT):
            dpad_x = -1

        return {
            "axes": {
                "l_x": -1 * c.get_axis(pygame.CONTROLLER_AXIS_LEFTX),
                "l_y": -1 * c.get_axis(pygame.CONTROLLER_AXIS_LEFTY),
                "r_x": -1 * c.get_axis(pygame.CONTROLLER_AXIS_RIGHTX),
            },
            "left_trigger": trigger(pygame.CONTROLLER_AXIS_TRIGGERLEFT),
            "right_trigger": trigger(pygame.CONTROLLER_AXIS_TRIGGERRIGHT),
            "dpad_y": dpad_y,
            "dpad_x": dpad_x,
            "buttons": {
                "A": bool(c.get_button(pygame.CONTROLLER_BUTTON_A)),
                "B": bool(c.get_button(pygame.CONTROLLER_BUTTON_B)),
                "X": bool(c.get_button(pygame.CONTROLLER_BUTTON_X)),
                "Y": bool(c.get_button(pygame.CONTROLLER_BUTTON_Y)),
                "LB": bool(c.get_button(pygame.CONTROLLER_BUTTON_LEFTSHOULDER)),
                "RB": bool(c.get_button(pygame.CONTROLLER_BUTTON_RIGHTSHOULDER)),
                "back": bool(c.get_button(pygame.CONTROLLER_BUTTON_BACK)),
                "start": bool(c.get_button(pygame.CONTROLLER_BUTTON_START)),
                "LStickButton": bool(
                    c.get_button(pygame.CONTROLLER_BUTTON_LEFTSTICK)
                ),
                "RStickButton": bool(
                    c.get_button(pygame.CONTROLLER_BUTTON_RIGHTSTICK)
                ),
            },
        }
