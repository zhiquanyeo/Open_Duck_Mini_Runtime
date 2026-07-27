"""
Interactively tune per-joint offsets (self.joints_offsets in hwi.py) with the keyboard.

Loads the current joints_offsets from ~/duck_config.json, stands the robot up,
lets you select a joint and nudge its offset in small increments while watching
it move live, then optionally writes the result back to ~/duck_config.json.
"""

import json
import os
import sys
import termios
import tty

from open_duck_mini_runtime.hardware.hwi import HWI
from open_duck_mini_runtime.duck_config import DuckConfig

STEP_SIZES = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05]
DEFAULT_STEP_INDEX = 2

CLEAR_SCREEN = "\033[2J\033[H"


def get_key() -> str:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch += sys.stdin.read(2)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch


def main():
    duck_config = DuckConfig()
    hwi = HWI(duck_config)
    joint_names = list(hwi.joints.keys())
    selected = 0
    step_index = DEFAULT_STEP_INDEX

    print("======")
    print("INTERACTIVE OFFSET ADJUSTMENT")
    print("======")
    print("Warning: this will stand the robot up using the current offsets.")
    print("Make sure it has room to move and is safe to power on.")
    print("======")
    input("Press Enter to enable motors and start...")

    hwi.set_kds([0] * len(hwi.joints))
    hwi.turn_on()

    def send(name):
        hwi.set_position(name, hwi.init_pos[name])

    def render():
        step = STEP_SIZES[step_index]
        print(CLEAR_SCREEN, end="")
        print("======")
        print("INTERACTIVE OFFSET ADJUSTMENT")
        print("UP/DOWN select | LEFT/RIGHT adjust | [ / ] step size | r reset | q quit")
        print(f"step size: {step:.3f} rad")
        print("======")
        for i, name in enumerate(joint_names):
            marker = "-> " if i == selected else "   "
            print(f"{marker}{name:16s} {hwi.joints_offsets[name]:+.4f}")
        print()

    render()

    try:
        while True:
            key = get_key()
            if key == "\x1b[A":  # up
                selected = (selected - 1) % len(joint_names)
            elif key == "\x1b[B":  # down
                selected = (selected + 1) % len(joint_names)
            elif key == "\x1b[C":  # right
                name = joint_names[selected]
                hwi.joints_offsets[name] += STEP_SIZES[step_index]
                send(name)
            elif key == "\x1b[D":  # left
                name = joint_names[selected]
                hwi.joints_offsets[name] -= STEP_SIZES[step_index]
                send(name)
            elif key == "]":
                step_index = min(step_index + 1, len(STEP_SIZES) - 1)
            elif key == "[":
                step_index = max(step_index - 1, 0)
            elif key == "r":
                name = joint_names[selected]
                hwi.joints_offsets[name] = 0.0
                send(name)
            elif key in ("q", "\x03"):  # q or Ctrl+C
                break
            render()
    finally:
        hwi.turn_off()

    print("Final offsets:")
    for name in joint_names:
        print(f'  "{name}": {hwi.joints_offsets[name]:.6f},')

    save = input("\nWrite these offsets to ~/duck_config.json? (y/N): ").strip().lower()
    if save == "y":
        config_path = os.path.expanduser("~/duck_config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
        else:
            config = {}
        config["joints_offsets"] = {
            name: float(f"{hwi.joints_offsets[name]:.6f}") for name in joint_names
        }
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
        print(f"Wrote offsets to {config_path}")
    else:
        print("Offsets not saved.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Turning off motors...")
        try:
            hwi = HWI(DuckConfig())
            hwi.turn_off()
            print("Motors turned off successfully.")
        except Exception as e:
            print(f"Error turning off motors: {e}")
