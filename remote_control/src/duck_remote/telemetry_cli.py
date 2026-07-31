"""
Poll the duck's stats server for a live joint-position/IMU/status dashboard.
Requires "web_stats": {"enabled": true} in the duck's duck_config.json.

Run with: uv run duck-remote-telemetry --host <duck-ip>
"""

import argparse
import json
import time
import urllib.error
import urllib.request

CLEAR_SCREEN = "\033[2J\033[H"


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=1) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _render(base, stats, telemetry):
    print(CLEAR_SCREEN, end="")
    print("======")
    print("DUCK-REMOTE TELEMETRY")
    print(f"polling {base} — Ctrl-C to quit")
    print("======")

    if stats is None and telemetry is None:
        print("unreachable — is web_stats.enabled true in duck_config.json?")
        print()
        return

    if stats is not None:
        print(f"mode:            {stats.get('mode')}")
        print(f"paused:          {stats.get('paused')}")
        print(f"motors_enabled:  {stats.get('motors_enabled')}")
        print(f"gamepad:         {stats.get('gamepad_connected')}")
        print(f"gait_freq_hz:    {stats.get('gait_frequency_hz')}")
    else:
        print("stats: unavailable")
    print()

    if telemetry is None:
        print("telemetry: unavailable")
        print()
        return

    imu = telemetry.get("imu") or {}
    print(f"gyro:            {imu.get('gyro')}")
    print(f"accel:           {imu.get('accel')}")
    print(f"gravity:         {imu.get('gravity')}")
    print(f"feet_contacts:   {telemetry.get('feet_contacts')}")
    print()

    joints = telemetry.get("joint_positions") or {}
    targets = telemetry.get("motor_targets") or {}
    print(f"{'joint':16s} {'position':>10s} {'target':>10s}")
    for name in targets:
        pos = joints.get(name)
        pos_str = f"{pos:+.3f}" if pos is not None else "n/a"
        print(f"{name:16s} {pos_str:>10s} {targets[name]:+10.3f}")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Duck's IP address")
    parser.add_argument("--port", type=int, default=8080, help="web_stats.port")
    parser.add_argument("--rate", type=float, default=5, help="poll rate in Hz")
    args = parser.parse_args()

    base = f"http://{args.host}:{args.port}"
    period = 1 / args.rate

    try:
        while True:
            stats = _get(f"{base}/stats")
            telemetry = _get(f"{base}/telemetry")
            _render(base, stats, telemetry)
            time.sleep(period)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
