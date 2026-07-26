"""
Minimal read-only stats webserver for the Open Duck Mini.

Serves a live JSON snapshot (control mode, pause state, gait frequency,
etc.) plus a small auto-refreshing HTML page. Built on the stdlib
http.server rather than a web framework — it's a handful of read-only
fields, and this is a Pi Zero 2W.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict

logger = logging.getLogger(__name__)

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
</style>
</head>
<body>
<h1>Open Duck Mini</h1>
<table id="stats"></table>
<div id="err"></div>
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
</script>
</body>
</html>
"""


class StatsServer:
    """Background HTTP server exposing a get_stats() callback as JSON + HTML."""

    def __init__(
        self,
        get_stats: Callable[[], Dict],
        host: str = "0.0.0.0",
        port: int = 8080,
    ):
        handler_cls = self._make_handler(get_stats)
        self._httpd = ThreadingHTTPServer((host, port), handler_cls)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Stats server listening on %s:%d", host, port)

    @staticmethod
    def _make_handler(get_stats: Callable[[], Dict]):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.debug("stats_server: " + fmt, *args)

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/stats":
                    body = json.dumps(get_stats()).encode()
                    self._send(200, body, "application/json")
                elif self.path == "/":
                    self._send(200, _PAGE, "text/html")
                else:
                    self._send(404, b"not found", "text/plain")

        return Handler

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
