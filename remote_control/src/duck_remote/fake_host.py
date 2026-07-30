"""
Local stand-in for the duck's RemoteController — binds the same UDP port and
decodes/pretty-prints packets, so you can sanity-check what duck-remote is
sending (axis values, button states, mode toggling, disconnect fail-safe)
without needing the actual robot.

Run in one terminal: uv run duck-remote-fake-host
Run in another:      uv run duck-remote --host 127.0.0.1

Mirrors (does not import) open_duck_mini_runtime.controller.remote_controller
so this package stays install-able without the duck runtime's dependencies.
"""

import argparse
import json
import logging
import socket
import time

from duck_remote.command_shaping import shape_commands

logger = logging.getLogger(__name__)

_RECV_BUFSIZE = 4096
_SOCKET_POLL_TIMEOUT = 0.1
_RENDER_INTERVAL = 0.05

CLEAR_SCREEN = "\033[2J\033[H"


def _render(host, port, connected, age, packet_count, head_control_mode,
            last_commands, left_trigger, right_trigger, buttons):
    print(CLEAR_SCREEN, end="")
    print("======")
    print("DUCK-REMOTE FAKE HOST")
    print(f"listening on {host}:{port} (UDP) — Ctrl-C to quit")
    print("======")
    if connected:
        print(f"status:    CONNECTED (last packet {age:.2f}s ago)")
    else:
        print("status:    DISCONNECTED (fail-safe active — commands zeroed)")
    print(f"packets:   {packet_count}")
    print(f"mode:      {'head-control' if head_control_mode else 'locomotion'}")
    print(f"commands:  {[round(c, 3) for c in last_commands]}")
    print(f"triggers:  L={left_trigger:.2f}  R={right_trigger:.2f}")
    pressed = [name for name, v in buttons.items() if v]
    print(f"buttons:   {', '.join(pressed) if pressed else '(none)'}")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=10000)
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.5,
        help="seconds without a packet before reporting disconnected",
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(_SOCKET_POLL_TIMEOUT)

    last_commands = [0.0] * 7
    head_control_mode = False
    prev_Y_pressed = False
    last_packet_time = 0.0
    packet_count = 0
    buttons = {}
    left_trigger = 0.0
    right_trigger = 0.0

    try:
        while True:
            try:
                data, _addr = sock.recvfrom(_RECV_BUFSIZE)
                try:
                    packet = json.loads(data)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning("dropping malformed packet: %s", e)
                    packet = None

                if packet is not None:
                    packet_count += 1
                    last_packet_time = time.time()

                    axes = packet.get("axes", {})
                    l_x = float(axes.get("l_x", 0.0))
                    l_y = float(axes.get("l_y", 0.0))
                    r_x = float(axes.get("r_x", 0.0))
                    left_trigger = float(packet.get("left_trigger", 0.0))
                    right_trigger = float(packet.get("right_trigger", 0.0))
                    buttons = packet.get("buttons", {})

                    Y_pressed = bool(buttons.get("Y", False))
                    if Y_pressed and not prev_Y_pressed:
                        head_control_mode = not head_control_mode
                    prev_Y_pressed = Y_pressed

                    last_commands = shape_commands(
                        l_x, l_y, r_x, head_control_mode, last_commands
                    )
            except socket.timeout:
                pass

            now = time.time()
            connected = bool(last_packet_time) and (
                now - last_packet_time
            ) < args.timeout
            if not connected:
                last_commands = [0.0] * 7
                buttons = {}
                left_trigger = 0.0
                right_trigger = 0.0

            _render(
                args.host,
                args.port,
                connected,
                now - last_packet_time if last_packet_time else 0.0,
                packet_count,
                head_control_mode,
                last_commands,
                left_trigger,
                right_trigger,
                buttons,
            )
            time.sleep(_RENDER_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
