import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .metrics import render_prometheus


def make_server(store, static_dir, max_age_s, port, bind="0.0.0.0"):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                body = (static_dir / "index.html").read_bytes()
                self._send(200, body, "text/html; charset=utf-8")
            elif path == "/status.json":
                doc = store.load()
                if doc is None:
                    self._json(503, {"error": "no data yet"})
                else:
                    self._json(200, doc)
            elif path == "/healthz":
                doc = store.load()
                try:
                    age = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(doc["generated_at"])).total_seconds()
                except (TypeError, KeyError, ValueError):
                    age = None
                ok = age is not None and age <= max_age_s
                self._json(200 if ok else 503, {"ok": ok, "age_s": age})
            elif path == "/metrics":
                self._send(200, render_prometheus(store.load()),
                          "text/plain; version=0.0.4; charset=utf-8")
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((bind, port), Handler)
