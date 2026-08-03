"""
Stats + remote-shutdown webserver for the Open Duck Mini.

Serves a live JSON snapshot (control mode, pause state, gait frequency,
etc.) plus a small auto-refreshing HTML page, and a POST /shutdown
endpoint to stop the duck-startup service without needing SSH access.
Built on the stdlib http.server rather than a web framework — it's a
handful of read-only fields, and this is a Pi Zero 2W.

Also optionally serves GET /telemetry (joint positions/IMU/foot contacts)
and GET/POST /eye_colors, for the remote_control PC-side tooling. Both are
no-ops (404) unless RLWalk passes the corresponding callback in.

And optionally GET /control (a phone-facing web control page) plus
POST /api/command, /api/button, /api/trim, /api/setting, backed by a
ControlBus instance RLWalk passes in — see control_bus.py. All 404 unless
a ControlBus is provided (i.e. unless web_stats is enabled).
"""

import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional

from open_duck_mini_runtime.rl_walk.control_bus import ControlBus

logger = logging.getLogger(__name__)

_WEBUI_DIR = Path(__file__).parent / "webui"
_CONTROL_PAGE_NOT_FOUND = (
    b"<!doctype html><p>control.html not installed, but the API is up.</p>"
)

# How long to wait for the process's own SIGTERM handler to clean up and
# exit before giving up on it and forcing the issue. Mirrors TimeoutStopSec
# in systemd/duck-startup.service so both shutdown paths behave the same.
_HARD_KILL_DELAY_S = 10.0


def _request_shutdown() -> None:
    logger.warning("Remote shutdown requested via /shutdown")
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(_HARD_KILL_DELAY_S)
    logger.warning(
        "Graceful shutdown did not finish within %.0fs, forcing exit",
        _HARD_KILL_DELAY_S,
    )
    os.kill(os.getpid(), signal.SIGKILL)


_PAGE = b"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Open Duck Mini - Stats</title>
<style>
  body { font-family: monospace; background: #111; color: #eee; padding: 2rem; }
  table { border-collapse: collapse; }
  td { padding: 0.25rem 1rem; }
  td.key { color: #8ab4f8; text-align: right; }
  #err { color: #f28b82; }
  #shutdown { margin-top: 1.5rem; padding: 0.5rem 1rem; background: #5c1a1a; color: #eee;
              border: 1px solid #f28b82; border-radius: 4px; cursor: pointer; font-family: inherit; }
  #shutdown:hover { background: #7a2323; }
  #shutdownMsg { margin-top: 0.5rem; color: #f28b82; }
</style>
</head>
<body>
<h1>Open Duck Mini</h1>
<p><a href="/control" style="color:#8ab4f8;">Open the control UI &rarr;</a></p>
<table id="stats"></table>
<div id="err"></div>
<button id="shutdown">Shutdown duck</button>
<div id="shutdownMsg"></div>
<script>
async function refresh() {
  try {
    const res = await fetch('/stats');
    const data = await res.json();
    document.getElementById('stats').innerHTML = Object.entries(data)
      .map(([k, v]) => `<tr><td class="key">${k}</td><td>${v}</td></tr>`)
      .join('');
    document.getElementById('err').textContent = '';
  } catch (e) {
    document.getElementById('err').textContent = 'stats unavailable: ' + e;
  }
}
refresh();
setInterval(refresh, 1000);

document.getElementById('shutdown').addEventListener('click', async () => {
  if (!confirm('Shut down the duck-startup service now?')) return;
  try {
    await fetch('/shutdown', { method: 'POST' });
    document.getElementById('shutdownMsg').textContent = 'Shutdown requested.';
  } catch (e) {
    document.getElementById('shutdownMsg').textContent = 'Shutdown request failed: ' + e;
  }
});
</script>
</body>
</html>
"""


class StatsServer:
    """Background HTTP server exposing a get_stats() callback as JSON + HTML,
    plus optional telemetry/eye-color callbacks for remote_control tooling."""

    def __init__(
        self,
        get_stats: Callable[[], Dict],
        host: str = "0.0.0.0",
        port: int = 8080,
        get_telemetry: Optional[Callable[[], Dict]] = None,
        get_eye_colors: Optional[Callable[[], Dict]] = None,
        set_eye_colors: Optional[Callable[[Dict], Dict]] = None,
        control_bus: Optional[ControlBus] = None,
    ):
        handler_cls = self._make_handler(
            get_stats, get_telemetry, get_eye_colors, set_eye_colors, control_bus
        )
        self._httpd = ThreadingHTTPServer((host, port), handler_cls)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Stats server listening on %s:%d", host, port)

    @staticmethod
    def _make_handler(
        get_stats: Callable[[], Dict],
        get_telemetry: Optional[Callable[[], Dict]],
        get_eye_colors: Optional[Callable[[], Dict]],
        set_eye_colors: Optional[Callable[[Dict], Dict]],
        control_bus: Optional[ControlBus],
    ):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.debug("stats_server: " + fmt, *args)

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, status: int, payload) -> None:
                self._send(status, json.dumps(payload).encode(), "application/json")

            def do_GET(self):
                if self.path == "/stats":
                    self._send_json(200, get_stats())
                elif self.path == "/telemetry":
                    if get_telemetry is None:
                        self._send(404, b"telemetry not available", "text/plain")
                    else:
                        self._send_json(200, get_telemetry())
                elif self.path == "/eye_colors":
                    if get_eye_colors is None:
                        self._send(
                            404, b"eye color control not available", "text/plain"
                        )
                    else:
                        self._send_json(200, get_eye_colors())
                elif self.path == "/control":
                    if control_bus is None:
                        self._send(
                            404, b"control UI not available", "text/plain"
                        )
                    else:
                        try:
                            body = (_WEBUI_DIR / "control.html").read_bytes()
                        except OSError:
                            body = _CONTROL_PAGE_NOT_FOUND
                        self._send(200, body, "text/html")
                elif self.path == "/":
                    self._send(200, _PAGE, "text/html")
                else:
                    self._send(404, b"not found", "text/plain")

            def _read_json_body(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"{}"
                return json.loads(raw) if raw else {}

            def do_POST(self):
                if self.path == "/shutdown":
                    self._send(202, b"shutdown requested", "text/plain")
                    # Respond before killing the process this handler is
                    # running in, otherwise the client never sees the 202.
                    threading.Thread(target=_request_shutdown, daemon=True).start()
                elif self.path == "/eye_colors":
                    if set_eye_colors is None:
                        self._send(
                            404, b"eye color control not available", "text/plain"
                        )
                        return
                    try:
                        result = set_eye_colors(self._read_json_body())
                        self._send_json(200, result)
                    except Exception as e:
                        self._send(400, str(e).encode(), "text/plain")
                elif self.path == "/api/command":
                    if control_bus is None:
                        self._send(404, b"control UI not available", "text/plain")
                        return
                    try:
                        d = self._read_json_body()
                        control_bus.set_command(
                            now=time.time(),
                            active=bool(d.get("active", False)),
                            l_x=d.get("l_x", 0.0),
                            l_y=d.get("l_y", 0.0),
                            r_x=d.get("r_x", 0.0),
                            r_y=d.get("r_y", 0.0),
                            left_trigger=d.get("left_trigger", 0.0),
                            right_trigger=d.get("right_trigger", 0.0),
                        )
                        self._send_json(200, {"ok": True})
                    except Exception as e:
                        self._send(400, str(e).encode(), "text/plain")
                elif self.path == "/api/button":
                    if control_bus is None:
                        self._send(404, b"control UI not available", "text/plain")
                        return
                    d = self._read_json_body()
                    ok = control_bus.push_button(
                        str(d.get("button", "")), str(d.get("action", "press"))
                    )
                    self._send_json(200 if ok else 400, {"ok": bool(ok)})
                elif self.path == "/api/trim":
                    if control_bus is None:
                        self._send(404, b"control UI not available", "text/plain")
                        return
                    d = self._read_json_body()
                    if str(d.get("action", "")) == "save":
                        control_bus.push_trim_save()
                        self._send_json(200, {"ok": True})
                        return
                    try:
                        ok = control_bus.push_trim(
                            str(d.get("axis", "")), float(d.get("delta", 0.0))
                        )
                    except (ValueError, TypeError):
                        self._send_json(400, {"ok": False, "error": "bad delta"})
                        return
                    self._send_json(200 if ok else 400, {"ok": bool(ok)})
                elif self.path == "/api/setting":
                    if control_bus is None:
                        self._send(404, b"control UI not available", "text/plain")
                        return
                    d = self._read_json_body()
                    group = str(d.get("group", ""))
                    action = str(d.get("action", ""))
                    if action == "save":
                        ok = control_bus.push_setting_save(group)
                    elif action == "reset":
                        ok = control_bus.push_setting_reset(group)
                    else:
                        ok = control_bus.push_setting(
                            group, str(d.get("key", "")), d.get("value")
                        )
                    self._send_json(200 if ok else 400, {"ok": bool(ok)})
                else:
                    self._send(404, b"not found", "text/plain")

        return Handler

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
