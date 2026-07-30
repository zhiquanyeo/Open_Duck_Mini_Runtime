"""
Reads a local gamepad and streams commands to a duck running with
remote_control.enabled=true in duck_config.json (see
open_duck_mini_runtime.controller.remote_controller.RemoteController).

Run with: uv run duck-remote --host <duck-ip>
"""

import argparse
import json
import logging
import socket
import time

from duck_remote.gamepad_reader import GamepadReader

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Duck's IP address")
    parser.add_argument("--port", type=int, default=10000)
    parser.add_argument("--rate", type=float, default=20, help="Send rate in Hz")
    parser.add_argument("--controller-index", type=int, default=0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    reader = GamepadReader(controller_index=args.controller_index)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)
    period = 1 / args.rate

    logger.info(
        "Relaying gamepad -> %s:%d at %.0f Hz (Ctrl-C to stop)",
        args.host,
        args.port,
        args.rate,
    )

    try:
        while True:
            t0 = time.time()

            packet = reader.poll()
            if packet is not None:
                sock.sendto(json.dumps(packet).encode(), target)
            else:
                logger.warning("No gamepad connected, not sending")

            elapsed = time.time() - t0
            time.sleep(max(0.0, period - elapsed))
    except KeyboardInterrupt:
        logger.info("Stopped")


if __name__ == "__main__":
    main()
