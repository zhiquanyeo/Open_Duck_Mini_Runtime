"""
NeoPixel brightness smoke-test.

Exercises the runtime brightness API added to ``LedController``
(``hardware/led_controller.py``): overall strip brightness plus independent
per-segment (left eye / right eye / projector) brightness multipliers.

Unlike ``test_neopixel.py`` (which talks to the ``neopixel`` library
directly), this drives ``LedController`` itself so it actually tests the
new ``set_brightness`` / ``set_left_eye_brightness`` / ``set_right_eye_brightness``
/ ``set_projector_brightness`` / ``set_eyes_brightness`` methods.

Run with:
  uv run tools/test_brightness.py
  uv run tools/test_brightness.py --count 3 1 1   # projector=3, left=1, right=1
  uv run tools/test_brightness.py --step 0.2 --hold 0.3
  uv run tools/test_brightness.py --target eyes        # sweep eyes only, projector held constant
  uv run tools/test_brightness.py --target projector   # sweep projector only, eyes held constant
"""

import argparse
import time

from open_duck_mini_runtime.hardware.led_controller import LedController

STEP_DEFAULT = 0.1
HOLD_DEFAULT = 0.15


def sweep(label: str, setter, step: float, hold: float) -> None:
    print(f"  {label}: 1.0 -> 0.0")
    v = 1.0
    while v >= 0.0:
        setter(round(v, 2))
        time.sleep(hold)
        v -= step
    setter(0.0)
    time.sleep(hold)

    print(f"  {label}: 0.0 -> 1.0")
    v = 0.0
    while v <= 1.0:
        setter(round(v, 2))
        time.sleep(hold)
        v += step
    setter(1.0)
    time.sleep(hold)


def main() -> None:
    parser = argparse.ArgumentParser(description="NeoPixel brightness test")
    parser.add_argument(
        "--count",
        nargs=3,
        type=int,
        metavar=("PROJECTOR", "LEFT_EYE", "RIGHT_EYE"),
        default=[1, 1, 1],
        help="Number of pixels per segment (default: 1 1 1)",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=STEP_DEFAULT,
        help=f"Brightness increment per tick (default: {STEP_DEFAULT})",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=HOLD_DEFAULT,
        help=f"Seconds to hold each brightness step (default: {HOLD_DEFAULT})",
    )
    parser.add_argument(
        "--target",
        choices=["all", "eyes", "projector"],
        default="all",
        help=(
            "Which segment(s) to step. 'eyes' sweeps left/right eye brightness "
            "and leaves the projector at --constant-brightness. 'projector' "
            "sweeps the projector and leaves both eyes at --constant-brightness. "
            "'all' (default) sweeps overall brightness plus every segment in turn."
        ),
    )
    parser.add_argument(
        "--constant-brightness",
        type=float,
        default=1.0,
        metavar="0.0-1.0",
        help="Brightness held on the segment(s) NOT being swept (default: 1.0)",
    )
    args = parser.parse_args()

    n_proj, n_left, n_right = args.count
    led_counts = {"projector": n_proj, "left_eye": n_left, "right_eye": n_right}

    print(f"Initialising LedController with led_counts={led_counts} ...")
    ctrl = LedController(led_counts=led_counts)

    try:
        ctrl.set_eyes_color("white")
        ctrl.set_projector_color("white")
        ctrl.set_eyes(True)
        ctrl.set_projector(True)
        ctrl.set_eyes_brightness(1.0)
        ctrl.set_projector_brightness(1.0)

        if args.target == "all":
            print(
                "\n[1/5] Overall strip brightness (set_brightness) — all segments dim together"
            )
            sweep("overall", ctrl.set_brightness, args.step, args.hold)

        if args.target in ("all", "eyes"):
            if args.target == "eyes":
                print(f"\nHolding projector at brightness={args.constant_brightness}")
                ctrl.set_projector_brightness(args.constant_brightness)

            print(
                "\n[2/5] Left eye only (set_left_eye_brightness) — "
                "right eye/projector stay constant"
            )
            sweep("left eye", ctrl.set_left_eye_brightness, args.step, args.hold)

            print(
                "\n[3/5] Right eye only (set_right_eye_brightness) — "
                "left eye/projector stay constant"
            )
            sweep("right eye", ctrl.set_right_eye_brightness, args.step, args.hold)

            print("\n[4/5] Both eyes together (set_eyes_brightness)")
            sweep("both eyes", ctrl.set_eyes_brightness, args.step, args.hold)

        if args.target in ("all", "projector"):
            if args.target == "projector":
                print(f"\nHolding both eyes at brightness={args.constant_brightness}")
                ctrl.set_eyes_brightness(args.constant_brightness)

            print(
                "\n[5/5] Projector only (set_projector_brightness) — eyes stay constant"
            )
            sweep("projector", ctrl.set_projector_brightness, args.step, args.hold)

        print("\nDone.")
    except KeyboardInterrupt:
        pass
    finally:
        ctrl.set_brightness(1.0)
        ctrl.set_eyes_brightness(1.0)
        ctrl.set_projector_brightness(1.0)
        ctrl.all_off()
        ctrl.deinit()
        print("Cleaned up.")


if __name__ == "__main__":
    main()
