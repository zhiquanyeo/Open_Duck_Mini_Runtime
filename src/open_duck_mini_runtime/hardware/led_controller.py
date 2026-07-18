"""
Shared controller for a single NeoPixel strip used by eyes and projector.

LED index layout
----------------
The strip is divided into three contiguous segments, in this order:
projector, right eye, left eye. Each segment's pixel count is
configurable (see ``led_counts`` in ``duck_config.json``) so eyes/
flashlight built from LED rings or clusters are supported, not just a
single pixel per feature.

Hardware imports (``board`` / ``neopixel``) are deferred until the first
:class:`LedController` instantiation so that the module can be safely
imported on non-Raspberry-Pi machines (e.g. during unit tests or
development on a laptop).
"""

from __future__ import annotations

import os
import atexit
from threading import Lock
from typing import Dict, Tuple, Optional, Union

# ---------------------------------------------------------------------------
# Configuration constants (resolved at import time using only stdlib / env)
# ---------------------------------------------------------------------------

# Segment pixel counts used when the caller doesn't supply its own
# (i.e. no duck_config.json ``led_counts`` section) — matches the
# original fixed 1/1/1 layout.
DEFAULT_LED_COUNTS: Dict[str, int] = {"projector": 1, "right_eye": 1, "left_eye": 1}

# Allow pixel order / brightness overrides via environment variables so they
# can be set in a systemd unit or SSH session without touching the code.
_ORDER_NAME: str = os.getenv("ODUCK_LED_ORDER", "GRBW").upper()
BRIGHTNESS: float = float(os.getenv("ODUCK_LED_BRIGHTNESS", "1.0"))
WHITE_MODE: str = os.getenv("ODUCK_LED_WHITE_MODE", "W").upper()


# ---------------------------------------------------------------------------
# LedController
# ---------------------------------------------------------------------------


class LedController:
    """Thread-safe NeoPixel manager for eyes and projector.

    Hardware imports (``board``, ``neopixel``) are performed inside
    ``__init__`` so importing this module never raises
    :exc:`ModuleNotFoundError` on non-Raspberry-Pi machines.
    """

    def __init__(self, led_counts: Optional[Dict[str, int]] = None) -> None:
        # --- Lazy hardware imports -------------------------------------------
        try:
            import board
            import neopixel as _neopixel
        except (ModuleNotFoundError, NotImplementedError) as exc:
            raise RuntimeError(
                "NeoPixel hardware libraries are not available. "
                "Ensure 'adafruit-circuitpython-neopixel' and 'rpi-ws281x' "
                "are installed and that you are running on the robot hardware."
            ) from exc

        counts = dict(DEFAULT_LED_COUNTS)
        if led_counts:
            counts.update(led_counts)
        for key, value in counts.items():
            if not isinstance(value, int) or value < 1:
                raise ValueError(
                    f"led_counts[{key!r}] must be a positive int, got {value!r}"
                )

        n_proj = counts["projector"]
        n_right = counts["right_eye"]
        n_left = counts["left_eye"]
        self._proj_slice = slice(0, n_proj)
        self._right_slice = slice(n_proj, n_proj + n_right)
        self._left_slice = slice(n_proj + n_right, n_proj + n_right + n_left)
        num_pixels = n_proj + n_right + n_left

        # Allow duck_config to override pixel order (env var takes priority).
        order_name = _ORDER_NAME
        try:
            from open_duck_mini_runtime.duck_config import LED_ORDER as _cfg_order  # type: ignore

            if _cfg_order:
                order_name = _cfg_order.upper()
        except Exception:
            pass

        white_mode = WHITE_MODE
        try:
            from open_duck_mini_runtime.duck_config import LED_WHITE_MODE as _cfg_wm  # type: ignore

            if _cfg_wm:
                white_mode = _cfg_wm.upper()
        except Exception:
            pass

        self._neopixel = _neopixel
        self._order = getattr(_neopixel, order_name, _neopixel.RGBW)
        self._white_mode = white_mode

        PIXEL_PIN = board.D10

        self._lock = Lock()
        self._deinited = False

        self._pixels = _neopixel.NeoPixel(
            PIXEL_PIN,
            num_pixels,
            brightness=BRIGHTNESS,
            auto_write=True,
            pixel_order=self._order,
        )

        # Cache simple colour tuples in logical (R, G, B, W) order.
        self.OFF = (0, 0, 0, 0)
        self.WHITE = (255, 255, 255, 0) if white_mode == "RGB" else (0, 0, 0, 255)
        self.RED = (255, 0, 0, 0)
        self.GREEN = (0, 255, 0, 0)
        self.BLUE = (0, 0, 255, 0)
        self.YELLOW = (255, 200, 0, 0)

        self._named_colors = {
            "off": self.OFF,
            "white": self.WHITE,
            "red": self.RED,
            "green": self.GREEN,
            "blue": self.BLUE,
            "yellow": self.YELLOW,
        }

        # On/off state (independent of colour)
        self.eye_left_on = True
        self.eye_right_on = True
        self.projector_on = False

        # Current colour per pixel (default WHITE)
        self.left_color = self.WHITE
        self.right_color = self.WHITE
        self.proj_color = self.WHITE

        self._apply()

        atexit.register(self.deinit)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply(
        self,
        left_color: Optional[Tuple] = None,
        right_color: Optional[Tuple] = None,
        proj_color: Optional[Tuple] = None,
    ) -> None:
        if self._pixels is None or self._deinited:
            return
        with self._lock:
            if left_color is None:
                left_color = self.left_color if self.eye_left_on else self.OFF
            if right_color is None:
                right_color = self.right_color if self.eye_right_on else self.OFF
            if proj_color is None:
                proj_color = self.proj_color if self.projector_on else self.OFF

            self._fill(self._proj_slice, proj_color)
            self._fill(self._right_slice, right_color)
            self._fill(self._left_slice, left_color)
            self._pixels.show()

    def _fill(self, segment: slice, color_rgba: Tuple) -> None:
        """Paint every pixel in ``segment`` the same colour."""
        value = self._to_order(color_rgba)
        self._pixels[segment] = [value] * (segment.stop - segment.start)

    def _to_order(self, color_rgba: Tuple) -> Tuple:
        """Strip the W channel for non-W strips; the neopixel library handles byte reordering."""
        r, g, b, w = color_rgba
        neopixel = self._neopixel
        try:
            if self._order in (neopixel.RGB, neopixel.GRB):
                return (r, g, b)
            return (r, g, b, w)
        except Exception:
            return (r, g, b, w)

    def _norm_color(
        self,
        color: Union[str, Tuple[int, int, int], Tuple[int, int, int, int]],
    ) -> Tuple[int, int, int, int]:
        if isinstance(color, str):
            c = self._named_colors.get(color.lower())
            if c is None:
                raise ValueError(f"Unknown colour name: {color!r}")
            return c
        if isinstance(color, tuple) and len(color) == 3:
            r, g, b = color
            return (r, g, b, 0)
        if isinstance(color, tuple) and len(color) == 4:
            return color
        raise ValueError("Colour must be a name string or (R,G,B) / (R,G,B,W) tuple")

    # ------------------------------------------------------------------
    # Eyes API
    # ------------------------------------------------------------------

    def set_eyes(self, on: bool) -> None:
        self.eye_left_on = on
        self.eye_right_on = on
        self._apply()

    def set_left_eye(self, on: bool) -> None:
        self.eye_left_on = on
        self._apply()

    def set_right_eye(self, on: bool) -> None:
        self.eye_right_on = on
        self._apply()

    def set_left_eye_color(self, color: Union[str, Tuple]) -> None:
        self.left_color = self._norm_color(color)
        self._apply()

    def set_right_eye_color(self, color: Union[str, Tuple]) -> None:
        self.right_color = self._norm_color(color)
        self._apply()

    def set_eyes_color(self, color: Union[str, Tuple]) -> None:
        norm = self._norm_color(color)
        self.left_color = norm
        self.right_color = norm
        self._apply()

    # ------------------------------------------------------------------
    # Projector API
    # ------------------------------------------------------------------

    def set_projector(self, on: bool) -> None:
        self.projector_on = on
        self._apply()

    def set_projector_color(self, color: Union[str, Tuple]) -> None:
        self.proj_color = self._norm_color(color)
        self._apply()

    # ------------------------------------------------------------------
    # Combined helpers
    # ------------------------------------------------------------------

    def set_all_color(self, color: Union[str, Tuple]) -> None:
        norm = self._norm_color(color)
        self.left_color = norm
        self.right_color = norm
        self.proj_color = norm
        self._apply()

    def all_off(self) -> None:
        self.eye_left_on = False
        self.eye_right_on = False
        self.projector_on = False
        self._apply()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def deinit(self) -> None:
        if self._deinited:
            return
        with self._lock:
            try:
                if self._pixels is not None:
                    self._pixels.fill(self._to_order(self.OFF))
                    self._pixels.show()
            except Exception:
                pass
            try:
                if self._pixels is not None:
                    self._pixels.deinit()
            finally:
                self._deinited = True
                self._pixels = None


# ---------------------------------------------------------------------------
# Module-level singleton helper
# ---------------------------------------------------------------------------

_controller: Optional[LedController] = None


def get_controller(led_counts: Optional[Dict[str, int]] = None) -> LedController:
    """Return (creating if necessary) the module-level :class:`LedController`.

    ``led_counts`` is only consulted the first time this creates the
    singleton; later calls (potentially with different counts) just
    return the already-initialised controller.
    """
    global _controller
    if _controller is None:
        _controller = LedController(led_counts=led_counts)
    return _controller
