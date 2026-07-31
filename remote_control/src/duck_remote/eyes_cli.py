"""
Set/preview the duck's eye color remotely, or read its current eye_colors
config, via the stats server's GET/POST /eye_colors endpoint. Requires
"web_stats": {"enabled": true} in the duck's duck_config.json.

Color changes are a live-only preview (see RLWalk.set_eye_color_preview) —
not persisted to duck_config.json, and reverted on the next pause/unpause
transition or restart.

Run with:
  uv run duck-remote-eyes --host <duck-ip> --color 255,0,0
  uv run duck-remote-eyes --host <duck-ip> --clear
  uv run duck-remote-eyes --host <duck-ip> --status
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def _request(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()}
    except urllib.error.URLError as e:
        return None, {"error": str(e.reason)}


def _parse_color(text):
    parts = text.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected R,G,B e.g. 255,0,0")
    try:
        return [int(p) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError("expected integers, e.g. 255,0,0")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Duck's IP address")
    parser.add_argument("--port", type=int, default=8080, help="web_stats.port")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--color", type=_parse_color, metavar="R,G,B", help="preview this color now"
    )
    group.add_argument(
        "--clear",
        action="store_true",
        help="stop the live preview, resume normal blinking",
    )
    group.add_argument(
        "--status", action="store_true", help="print the duck's eye_colors config"
    )
    args = parser.parse_args()

    base = f"http://{args.host}:{args.port}"

    if args.status:
        status, body = _request(f"{base}/eye_colors")
    elif args.clear:
        status, body = _request(
            f"{base}/eye_colors", method="POST", body={"clear": True}
        )
    else:
        status, body = _request(
            f"{base}/eye_colors", method="POST", body={"color": args.color}
        )

    if status is None:
        print(f"Could not reach {base}: {body['error']}", file=sys.stderr)
        sys.exit(1)
    if status >= 400:
        print(f"Duck returned {status}: {body.get('error', body)}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(body, indent=2))


if __name__ == "__main__":
    main()
