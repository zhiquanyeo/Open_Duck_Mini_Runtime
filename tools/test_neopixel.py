"""
Quick NeoPixel strip smoke-test.

LED layout (right eye, left eye, projector — 1 pixel each by default):
  index 0   → right eye
  index 1   → left eye
  index 2   → projector

Run with:
  uv run tools/test_neopixel.py
  uv run tools/test_neopixel.py --count 3 1 1   # projector=3, left=1, right=1
"""

import argparse
import time

import board
import neopixel

PIXEL_PIN = board.D10
PIXEL_ORDER = neopixel.GRB
BRIGHTNESS = 1.0


def main() -> None:
    parser = argparse.ArgumentParser(description="NeoPixel strip test")
    parser.add_argument(
        "--count",
        nargs=3,
        type=int,
        metavar=("PROJECTOR", "LEFT_EYE", "RIGHT_EYE"),
        default=[1, 1, 1],
        help="Number of pixels per segment (default: 1 1 1)",
    )
    args = parser.parse_args()

    n_proj, n_left, n_right = args.count
    num_pixels = n_right + n_left + n_proj

    right_slice = slice(0, n_right)
    left_slice = slice(n_right, n_right + n_left)
    proj_slice = slice(n_right + n_left, n_right + n_left + n_proj)

    print(f"Initialising {num_pixels}-pixel strip on {PIXEL_PIN} ...")
    pixels = neopixel.NeoPixel(
        PIXEL_PIN,
        num_pixels,
        brightness=BRIGHTNESS,
        auto_write=False,
        pixel_order=PIXEL_ORDER,
    )

    segments = [
        ("right eye", right_slice, (0, 255, 0)),      # green
        ("left eye",  left_slice, (0, 0, 255)),       # blue
        ("projector", proj_slice, (255, 255, 255)),   # white
    ]

    print("Cycling through segments — Ctrl-C to exit")
    try:
        while True:
            for name, seg, color in segments:
                pixels.fill((0, 0, 0))
                pixels[seg] = [color] * (seg.stop - seg.start)
                pixels.show()
                print(f"  {name}")
                time.sleep(0.8)
    except KeyboardInterrupt:
        pass
    finally:
        pixels.fill((0, 0, 0))
        pixels.show()
        pixels.deinit()
        print("\nDone.")


if __name__ == "__main__":
    main()
