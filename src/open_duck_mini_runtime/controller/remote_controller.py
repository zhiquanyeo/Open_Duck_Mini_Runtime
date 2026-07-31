"""
Network-sourced stand-in for XBoxController.

Exposes the same runtime surface RLWalk needs (`.connected`,
`.get_last_command()`) but is fed by small JSON/UDP packets from a
PC-side relay app (see the top-level `remote_control/` package) instead
of a locally paired gamepad. Axis-to-command scaling and range clamping
happen here, on the duck, via the same `shape_commands()` used by
XBoxController — so a remote client only ever needs to send raw
normalized joystick values, and can't push the robot outside its
configured physical limits even if it sends garbage.
"""

import json
import logging
import socket
import threading
import time
from queue import Queue

from open_duck_mini_runtime.controller.buttons import Buttons
from open_duck_mini_runtime.controller.command_shaping import shape_commands

logger = logging.getLogger(__name__)

_RECV_BUFSIZE = 4096
_SOCKET_POLL_TIMEOUT = 0.2  # s, how often the recv loop checks for new packets


class RemoteController:
    def __init__(
        self,
        command_freq,
        host="0.0.0.0",
        port=10000,
        only_head_control=False,
        timeout=0.5,
    ):
        self.command_freq = command_freq
        self.only_head_control = only_head_control
        self.head_control_mode = only_head_control
        # How long without a packet before we consider the link dead and
        # fail safe (zero commands, release all buttons).
        self.timeout = timeout

        self.last_commands = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.last_left_trigger = 0.0
        self.last_right_trigger = 0.0
        self.connected = False

        self.buttons = Buttons()
        self.cmd_queue = Queue(maxsize=1)

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._sock.settimeout(_SOCKET_POLL_TIMEOUT)

        self._lock = threading.Lock()
        self._latest_packet = None
        self._latest_packet_time = 0.0
        self._prev_Y_pressed = False

        threading.Thread(target=self._recv_worker, daemon=True).start()
        threading.Thread(target=self._commands_worker, daemon=True).start()
        logger.info("RemoteController listening on %s:%d (UDP)", host, port)

    def _recv_worker(self):
        while True:
            try:
                data, _addr = self._sock.recvfrom(_RECV_BUFSIZE)
            except socket.timeout:
                continue
            except OSError as e:
                logger.warning("RemoteController socket error: %s", e)
                continue

            try:
                packet = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("RemoteController: dropping malformed packet: %s", e)
                continue

            with self._lock:
                self._latest_packet = packet
                self._latest_packet_time = time.time()

    def _commands_worker(self):
        while True:
            self.cmd_queue.put(self.get_commands())
            time.sleep(1 / self.command_freq)

    def get_commands(self):
        now = time.time()
        with self._lock:
            packet = self._latest_packet
            packet_time = self._latest_packet_time

        self.connected = bool(packet_time) and (now - packet_time) < self.timeout

        if not self.connected:
            # Fail safe: a lost link should not leave the duck executing a
            # stale command forever.
            self.last_commands = [0.0] * 7
            return (
                list(self.last_commands),
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                0.0,
                0.0,
                0,
                0,
            )

        axes = packet.get("axes", {})
        l_x = float(axes.get("l_x", 0.0))
        l_y = float(axes.get("l_y", 0.0))
        r_x = float(axes.get("r_x", 0.0))

        left_trigger = float(packet.get("left_trigger", 0.0))
        right_trigger = float(packet.get("right_trigger", 0.0))

        buttons_in = packet.get("buttons", {})
        Y_pressed = bool(buttons_in.get("Y", False))
        if Y_pressed and not self._prev_Y_pressed and not self.only_head_control:
            self.head_control_mode = not self.head_control_mode
        self._prev_Y_pressed = Y_pressed

        self.last_commands = shape_commands(
            l_x, l_y, r_x, self.head_control_mode, self.last_commands
        )

        up_down = int(packet.get("dpad_y", 0))
        left_right = int(packet.get("dpad_x", 0))

        return (
            list(self.last_commands),
            bool(buttons_in.get("A", False)),
            bool(buttons_in.get("B", False)),
            bool(buttons_in.get("X", False)),
            Y_pressed,
            bool(buttons_in.get("LB", False)),
            bool(buttons_in.get("RB", False)),
            bool(buttons_in.get("start", False)),
            bool(buttons_in.get("back", False)),
            bool(buttons_in.get("LStickButton", False)),
            bool(buttons_in.get("RStickButton", False)),
            left_trigger,
            right_trigger,
            up_down,
            left_right,
        )

    def get_last_command(self):
        A_pressed = False
        B_pressed = False
        X_pressed = False
        Y_pressed = False
        LB_pressed = False
        RB_pressed = False
        start_pressed = False
        back_pressed = False
        LStickButton_pressed = False
        RStickButton_pressed = False
        up_down = 0
        left_right = 0
        try:
            (
                self.last_commands,
                A_pressed,
                B_pressed,
                X_pressed,
                Y_pressed,
                LB_pressed,
                RB_pressed,
                start_pressed,
                back_pressed,
                LStickButton_pressed,
                RStickButton_pressed,
                self.last_left_trigger,
                self.last_right_trigger,
                up_down,
                left_right,
            ) = self.cmd_queue.get(
                False
            )  # non blocking
        except Exception:
            pass

        self.buttons.update(
            A_pressed,
            B_pressed,
            X_pressed,
            Y_pressed,
            LB_pressed,
            RB_pressed,
            up_down == 1,
            up_down == -1,
            start=start_pressed,
            back=back_pressed,
            LStickButton=LStickButton_pressed,
            RStickButton=RStickButton_pressed,
            dpad_left=left_right == -1,
            dpad_right=left_right == 1,
        )

        return (
            self.last_commands,
            self.buttons,
            self.last_left_trigger,
            self.last_right_trigger,
        )
