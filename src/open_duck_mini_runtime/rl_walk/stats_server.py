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
"""

import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

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
    ):
        handler_cls = self._make_handler(
            get_stats, get_telemetry, get_eye_colors, set_eye_colors
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
                elif self.path == "/":
                    self._send(200, _PAGE, "text/html")
                else:
                    self._send(404, b"not found", "text/plain")

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
                        length = int(self.headers.get("Content-Length", 0) or 0)
                        raw = self.rfile.read(length) if length else b"{}"
                        data = json.loads(raw) if raw else {}
                        result = set_eye_colors(data)
                        self._send_json(200, result)
                    except Exception as e:
                        self._send(400, str(e).encode(), "text/plain")
                else:
                    self._send(404, b"not found", "text/plain")

        return Handler

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
